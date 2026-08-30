# notion-icloud-sync

노션 "캘린더" 데이터베이스에 추가한 일정을 10분마다 자동으로 iCloud 캘린더("노션 일정"이라는 이름으로 새로 생김)에 동기화합니다. 아이폰은 원래 쓰던 iCloud 계정 그대로 쓰면 되고, 별도 앱 설치나 구독(ICS) 없이 순정 캘린더 앱에 그대로 뜹니다.

## 설정 방법 (한 번만 하면 됨)

### 1. Notion 통합(integration) 만들기
1. https://www.notion.so/my-integrations 접속
2. **New integration** 클릭 → 이름 아무거나(예: `icloud-sync`) → 워크스페이스 선택 → 만들기
3. 생성된 **Internal Integration Secret**(토큰) 복사해두기

### 2. 캘린더 데이터베이스에 통합 연결
1. "슬기로운 인하생활" 대시보드에서 "캘린더" 데이터베이스 페이지로 이동 (학사일정/다가오는 일정에 쓰이는 그 DB)
2. 우측 상단 `•••` 메뉴 → **연결(Connections)** → 방금 만든 통합(`icloud-sync`) 추가

### 3. 데이터베이스 ID 확인
캘린더 데이터베이스의 데이터소스 URL에서 `collection://` 뒤의 UUID 부분이 `NOTION_DATABASE_ID`입니다.
(예: `collection://e88e78e9-0958-82d9-a768-0758b88e035d` → `e88e78e9-0958-82d9-a768-0758b88e035d`)

### 4. Apple 앱 암호 만들기
1. https://appleid.apple.com 로그인
2. **로그인 및 보안** → **앱 암호** → 암호 생성 → 이름 아무거나(예: `notion-sync`)
3. 생성된 16자리 암호 복사해두기 (형식: `xxxx-xxxx-xxxx-xxxx`)

### 5. GitHub 저장소에 Secrets 등록
이 저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret** 에서 아래 4개를 각각 등록:

| Secret 이름 | 값 |
|---|---|
| `NOTION_TOKEN` | 1번에서 복사한 Internal Integration Secret |
| `NOTION_DATABASE_ID` | 3번에서 확인한 데이터베이스 ID |
| `ICLOUD_APPLE_ID` | iCloud 로그인 이메일 |
| `ICLOUD_APP_PASSWORD` | 4번에서 만든 앱 암호 |

### 6. 확인
**Actions** 탭 → **Sync Notion calendar to iCloud** → **Run workflow** 로 수동 실행해보고 로그 확인. 성공하면 아이폰 캘린더 앱에 "노션 일정"이라는 새 캘린더가 생기고 그 안에 항목들이 채워집니다 (설정 → 캘린더 → 캘린더 계정에서 "노션 일정" 캘린더가 켜져 있는지도 확인하세요).

이후로는 10분마다 자동 실행되어, 노션에서 추가/수정한 일정이 알아서 반영됩니다.
