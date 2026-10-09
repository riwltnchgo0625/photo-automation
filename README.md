# Photo Assist 결과 수집기 — 두 가지 실행 방식

각 폴더가 독립된 프로젝트입니다. 한 가지만 설치해도 됩니다.

| 구분 | appium_version | no_node_version |
|---|---|---|
| 자동화 도구 | Appium | Python uiautomator2(openatx) |
| Node.js/npm | 필요 | 불필요 |
| Docker | 불필요 | 불필요 |
| Python | 필요 | 필요 |
| ADB 및 USB 디버깅 | 필요 | 필요 |
| PC의 Appium 서버 | 코드가 시작/종료 | 사용 안 함 |
| 기기 내부 보조 서비스 | 필요 | 필요 |
| 결과 보관 | 원본과 같은 갤러리 폴더 | 원본과 같은 갤러리 폴더 |

Node 없는 버전은 ‘Appium에서 Node만 제거한 것’이 아니라 다른 라이브러리 구현입니다.
보안 제한을 우회하지 않으며, 회사 사용 허용 여부는 별도입니다.
두 버전 모두 최초 패키지 설치가 필요합니다. uiautomator2는 연결 시 기기에 보조 JAR 등을
배치/실행할 수 있습니다. Node가 없다는 뜻이지 보조 서비스나 설치가 전혀 없다는 뜻은 아닙니다.

## 공통 동작

선택한 원본 ID → Photo Assist → 스타일 → 생성 완료 → 다른 파일로 저장 →
원본 폴더의 새 파일 확인 → 같은 원본 다시 열기 → 다음 스타일.
원본/스타일 목록을 설정 파일에 지정합니다. 결과 사진은 PC로 복사하지 않습니다.
실행 이력과 실패 화면은 PC의 run_data에 남깁니다.
실패하면 멈추고 pending 기록을 남겨 중복 저장를 방지합니다.
실제 기기 버튼 식별자 및 전체 스타일 목록은 아직 미확정입니다.
기존 영상만으로 resource-id를 확인할 수 없으므로 CHANGE 설정을 반드시 보정해야 합니다.
**실제 S26+ / Windows E2E 실행은 아직 검증하지 않았습니다.**

## 가상환경 없이 시작하기

`no_node_version` 폴더에서 아래 명령을 실행합니다.

```powershell
python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

설정 파일 복사는 처음 한 번만 합니다. 이미 수정한 config.json을 덮어쓰지 마세요.
ADB 연결과 버튼 설정을 완료한 후 `python collector.py run` 또는 run.bat으로 실행합니다.
아래의 가상환경 방식은 패키지를 분리하고 싶은 경우 선택합니다.

## Node 없는 버전 시작

1. Android SDK Platform-Tools를 공식 사이트에서 받아 압축을 풀고 PATH에 등록합니다.
   https://developer.android.com/tools/releases/platform-tools
2. 폰의 USB 디버깅을 켜고 연결 승인 후 `adb devices -l`에서 device 상태를 확인합니다.
3. `no_node_version` 폴더에서 PowerShell을 열고 실행합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

config.json의 udid를 실제 일련번호로 수정합니다.
갤러리 패키지 기본값은 com.sec.android.gallery3d이며 실제와 다르면 수정합니다.
이 버전은 PC에 별도 JDK나 Node.js를 설치하지 않아도 되는 구성입니다.
Python 3.14에서 의존 패키지 설치 문제가 생기면 오류를 확인한 후 호환 버전을 결정합니다.
현재 제공 환경에서는 Windows/Python 3.14 설치를 검증하지 않았습니다.

## Appium 버전 시작

`appium_version` 폴더를 열어 Python 가상환경/패키지/config.json을 위와 동일하게 준비합니다.
추가로 Node.js LTS(Appium 요구 버전), JDK 17 및 Android SDK를 준비하고
JAVA_HOME, ANDROID_HOME 및 platform-tools PATH를 설정합니다.

```powershell
npm.cmd install -g appium@3
appium.cmd driver install uiautomator2
appium.cmd driver doctor uiautomator2
```

이번 버전은 run/dump에서 Appium 서버를 자동 시작합니다. 기본 주소는 127.0.0.1:4723이며
외부 네트워크에 서버를 공개하지 않습니다. 기존 서버가 있으면 /status를 확인해 재사용하고
그 서버는 종료하지 않습니다. 직접 켠 서버만 정상 종료/Ctrl+C 처리 때 종료합니다.
프로세스를 강제로 종료한 경우에는 서버가 남을 수 있습니다.
Appium 서버 로그는 해당 폴더 appium_server.log에 남습니다.

## 원본과 버튼 설정 — 두 버전 공통

해당 버전 폴더에서:

```powershell
.\.venv\Scripts\python.exe collector.py scan
.\.venv\Scripts\python.exe collector.py dump
```

- scan: 전체 사진 ID/경로를 run_data/media.json에 기록. config.json의 album_dir와
  original_ids를 실제 값으로 지정합니다. 예시 원본 ID를 임의로 쓰지 마세요.
- dump: 현재 기기 화면 XML/PNG 저장. 원본 화면, 스타일 화면, 더보기 화면, 생성 완료 화면에서
  각각 실행하여 버튼을 확인합니다. Node 없는 버전도 Inspector 없이 이 명령으로 확인 가능합니다.
- entry_steps: 포토 어시스트 버튼 → 스타일 탭.
- styles: **실제 기기의 모든 스타일을 순서대로 입력**. 예시 2개는 전체 목록이 아닙니다.
  각 스타일 steps는 초기 스타일 화면부터 시작해 더보기/분류/스크롤/선택 순서를 포함합니다.
- locator의 by: id(resource-id), accessibility id(content-desc), xpath 중 하나.
  값은 실제 XML을 보고 넣습니다. 두 라이브러리의 XML은 차이가 있을 수 있어 각각 확인합니다.
- `server_url`은 Appium 버전만 사용합니다.
- 최초 약관·계정 로그인은 직접 완료합니다. Photo Assist 생성에 필요한 폰 인터넷도 유지합니다.

더보기 클릭 및 스와이프 예시(실제 화면에 맞춰 수정):

```json
[
  {"action": "click", "locator": {"by": "xpath", "value": "//*[@text='더보기']"}},
  {"action": "swipe", "points": [0.85, 0.82, 0.20, 0.82]},
  {"action": "click", "locator": {"by": "id", "value": "실제 resource-id"}}
]
```

스와이프는 화면 너비/높이 대비 비율입니다. 사진 영역이 아닌 스타일 목록에 맞춰야 합니다.

## 한 번에 실행

설치 및 설정이 끝난 후 폰을 USB 연결하고 잠금 해제한 뒤 각 폴더의 `run.bat`을 더블클릭합니다.
가상환경은 선택 사항입니다. run.bat은 .venv가 있으면 그 파이썬을, 없으면 PATH의 python을 사용합니다. 두 버전을 같은 폰에 동시에 실행하지 마세요.
먼저 원본 1장/스타일 1개로 설정하여 실제 저장 위치와 원본 재진입부터 확인하세요.
기능 품질은 사람이 판단합니다. 프로그램은 새 파일 저장을 확인할 뿐 정성평가하지 않습니다.

실행 목록만 확인할 때:

```powershell
.\.venv\Scripts\python.exe collector.py plan
```

## 기록과 복구

run_data/state.json에 완료 결과 경로/소요시간과 pending을 기록합니다.
동일 설정의 재실행은 완료된 항목을 건너뜁니다.
pending이 있으면 자동 재생성하지 않습니다. 갤러리에서 해당 스타일 결과의 유무를 확인하세요.
결과가 없다는 것을 확인한 경우에만 state.json을 백업하고 pending을 null로 바꿔 재시도합니다.
이미 저장됐다면 완료 기록과 결과를 대조하여 복구해야 합니다. 상태 파일을 무작정 지우지 마세요.
설정이 바뀌면 다른 --work 폴더로 새 작업을 시작하되 기존 결과와 중복될 수 있습니다.
두 버전의 상태는 별개이므로 한쪽 완료 이력이 다른 쪽 실행을 막지 않습니다.

## 제한

- 저장 직전/직후 MediaStore 차이로 결과를 연결합니다. 카메라 촬영·사진 다운로드·동기화를
  병행하지 마세요. 다른 작업이 만든 단일 사진을 결과로 오인할 가능성이 있습니다.
- 원본과 다른 폴더 저장, 원본 메타데이터 변경, 여러 새 이미지 발생 시 중단합니다.
- MediaStore 조회/원본 content URI 열기는 기기 권한과 펌웨어에 따라 확인이 필요합니다.
- 원본 보존 확인은 경로/크기/수정시간 기반이며 이미지 해시 비교는 아닙니다.
- 생성 실패/적용 불가 스타일/추가 팝업은 자동 우회하지 않고 시간 초과로 중단합니다.
- run_data와 config.json에는 개인 경로와 단말 정보가 있으므로 공개 저장소에서 제외합니다.

## 테스트

각 버전 폴더에서 `python -m unittest -v`를 실행합니다.
저장 판정 테스트와 백엔드 모의 테스트는 기기 없이 수행되며 실제 기기 성공을 보장하지 않습니다.

## 공식 참고자료

- https://github.com/openatx/uiautomator2
- https://github.com/appium/appium-uiautomator2-driver
- https://github.com/appium/python-client
- https://appium.io/docs/en/latest/quickstart/requirements/
