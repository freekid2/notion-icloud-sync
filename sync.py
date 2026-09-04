#!/usr/bin/env python3
"""
Notion '캘린더' 데이터베이스 -> iCloud 캘린더 동기화 스크립트

동작 방식:
  - Notion API로 캘린더 데이터베이스의 모든 행을 읽는다.
  - 각 행을 iCloud 캘린더(CalDAV)에 UID = "notion-<page_id>@notion-sync" 로 upsert 한다.
    (같은 UID로 PUT 하면 기존 이벤트를 덮어쓰므로, 여러 번 실행해도 중복 생성되지 않는다.)
  - "노션 일정"이라는 이름의 전용 캘린더를 자동으로 찾거나 새로 만들어서, 사용자의 기존
    개인 캘린더를 어지럽히지 않는다.

필요한 환경변수 (GitHub Actions secrets로 주입):
  NOTION_TOKEN           - Notion internal integration 토큰
  NOTION_DATABASE_ID     - 동기화할 '캘린더' 데이터소스 ID (collection:// 뒤의 UUID)
  ICLOUD_APPLE_ID        - iCloud 로그인 이메일
  ICLOUD_APP_PASSWORD    - appleid.apple.com에서 생성한 앱 암호
"""

import os
import sys
import hashlib
from datetime import datetime, date, timedelta

import requests
import caldav
from icalendar import Calendar as ICalendar, Event as ICalEvent

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
ICLOUD_APPLE_ID = os.environ["ICLOUD_APPLE_ID"]
ICLOUD_APP_PASSWORD = os.environ["ICLOUD_APP_PASSWORD"]

CALENDAR_NAME = "노션 일정"
NOTION_API_VERSION = "2025-09-03"
DATE_PROPERTY_CANDIDATES = ["과제기한", "시험일시", "날짜", "date"]  # 데이터소스마다 이름이 다를 수 있어 순서대로 시도
TITLE_PROPERTY_CANDIDATES = ["이름", "Name", "title"]
COMPLETED_PROPERTY_CANDIDATES = ["완료", "Done"]


def notion_query_database(database_id):
    """Notion 데이터소스의 모든 페이지를 페이지네이션 처리하며 가져온다.
    (2025-09-03 API부터 데이터베이스는 여러 데이터소스를 가질 수 있어,
    /v1/databases/{id}/query 대신 /v1/data_sources/{id}/query 를 사용한다.)"""
    url = f"https://api.notion.com/v1/data_sources/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    results = []
    payload = {"page_size": 100}
    while True:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]
    return results


def extract_title(properties):
    for key in TITLE_PROPERTY_CANDIDATES:
        prop = properties.get(key)
        if prop and prop.get("type") == "title":
            texts = prop.get("title", [])
            if texts:
                return "".join(t.get("plain_text", "") for t in texts)
    return None


def extract_date(properties):
    for key in DATE_PROPERTY_CANDIDATES:
        prop = properties.get(key)
        if prop and prop.get("type") == "date" and prop.get("date"):
            d = prop["date"]
            return d.get("start"), d.get("end")
    return None, None


def extract_completed(properties):
    """'완료' 체크박스가 켜져 있는지 확인한다. Notion 쪽 뷰 필터(완료=FALSE)는
    화면에만 적용될 뿐 API 조회 결과에는 영향이 없으므로, 완료된 항목을 iCloud에서도
    치우려면 여기서 직접 걸러줘야 한다."""
    for key in COMPLETED_PROPERTY_CANDIDATES:
        prop = properties.get(key)
        if prop and prop.get("type") == "checkbox":
            return bool(prop.get("checkbox"))
    return False


def parse_notion_datetime(value):
    """Notion의 ISO 날짜/날짜시간 문자열을 datetime 또는 date 객체로 변환."""
    if value is None:
        return None
    if len(value) == 10:  # "YYYY-MM-DD" 형태 -> 종일 이벤트
        return date.fromisoformat(value)
    # datetime (타임존 포함)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_ical(uid, title, start, end):
    cal = ICalendar()
    cal.add("prodid", "-//notion-icloud-sync//KR")
    cal.add("version", "2.0")

    event = ICalEvent()
    event.add("uid", uid)
    event.add("summary", title)
    event.add("dtstart", start)

    is_all_day = isinstance(start, date) and not isinstance(start, datetime)

    if end is not None:
        event.add("dtend", end)
    elif is_all_day:
        event.add("dtend", start + timedelta(days=1))
    else:
        event.add("dtend", start + timedelta(hours=1))

    event.add("dtstamp", datetime.utcnow())
    cal.add_component(event)
    return cal.to_ical().decode("utf-8")


def get_or_create_calendar(principal):
    for cal in principal.calendars():
        try:
            if cal.name == CALENDAR_NAME:
                return cal
        except Exception:
            continue
    return principal.make_calendar(name=CALENDAR_NAME)


def get_existing_notion_events(calendar):
    """이 스크립트가 동기화한(uid가 notion-<id>@notion-sync 형태인) 기존 iCloud
    이벤트를 uid -> caldav Event 형태로 모두 가져온다. 삭제된 Notion 페이지에 대응하는
    이벤트를 정리(clean up)하는 데 사용한다."""
    existing = {}
    for event in calendar.events():
        try:
            ical = ICalendar.from_ical(event.data)
        except Exception:
            continue
        for component in ical.walk():
            if component.name != "VEVENT":
                continue
            uid = str(component.get("uid", ""))
            if uid.startswith("notion-") and uid.endswith("@notion-sync"):
                existing[uid] = event
    return existing


def main():
    print("Notion 데이터베이스 조회 중...")
    pages = notion_query_database(NOTION_DATABASE_ID)
    print(f"  -> {len(pages)}개 항목 발견")

    print("iCloud CalDAV 연결 중...")
    client = caldav.DAVClient(
        url="https://caldav.icloud.com/",
        username=ICLOUD_APPLE_ID,
        password=ICLOUD_APP_PASSWORD,
    )
    principal = client.principal()
    calendar = get_or_create_calendar(principal)
    print(f"  -> 캘린더 '{CALENDAR_NAME}' 준비 완료")

    synced, skipped, completed = 0, 0, 0
    valid_uids = set()
    for page in pages:
        page_id = page["id"]
        properties = page.get("properties", {})

        title = extract_title(properties)
        start_raw, end_raw = extract_date(properties)

        if not title or not start_raw:
            skipped += 1
            continue

        if extract_completed(properties):
            # 완료 체크된 항목은 iCloud에 올리지 않는다. valid_uids에도 넣지 않으므로,
            # 이미 동기화되어 있던 경우 아래 "삭제된 Notion 항목 정리" 단계에서 지워진다.
            completed += 1
            continue

        start = parse_notion_datetime(start_raw)
        end = parse_notion_datetime(end_raw) if end_raw else None

        uid = f"notion-{page_id}@notion-sync"
        valid_uids.add(uid)
        ical_text = build_ical(uid, title, start, end)

        # iCloud는 event_by_uid()가 쓰는 REPORT 기반 UID 조회가 불안정해서
        # 412 Precondition Failed로 실패하는 경우가 있다. 대신 UID가 이미
        # 포함된 ical을 add_event로 바로 PUT해서 생성/덮어쓰기(upsert)한다.
        calendar.add_event(ical_text, no_overwrite=False, no_create=False)

        synced += 1

    # Notion에서 삭제되었거나(휴지통 포함), 페이지 재생성 등으로 uid가 바뀐 항목은
    # notion_query_database()의 결과(pages)에 더는 나타나지 않는다. 이런 "고아"
    # 이벤트를 iCloud 캘린더에서 찾아 정리해서, 지운 일정이 계속 남아있지 않게 한다.
    print("삭제된 Notion 항목 정리 중...")
    existing_notion_events = get_existing_notion_events(calendar)
    deleted = 0
    for uid, event in existing_notion_events.items():
        if uid in valid_uids:
            continue
        try:
            event.delete()
            deleted += 1
        except Exception as exc:
            print(f"  -> {uid} 삭제 실패: {exc}", file=sys.stderr)

    print(
        f"완료: {synced}개 동기화, {skipped}개 건너뜀(제목/날짜 없음), "
        f"{completed}개 제외(완료 체크됨), {deleted}개 삭제(Notion에서 제거/완료된 항목)"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"오류 발생: {exc}", file=sys.stderr)
        sys.exit(1)
