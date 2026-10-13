# Photo Assist 결과 수집기

갤럭시에서 원본 사진마다 스타일을 적용하고, 결과를 **원본과 같은 갤러리 폴더에 저장**하는 Windows 프로그램입니다. 결과 사진의 품질은 사람이 평가합니다.

## 사용자가 실행하는 방법

1. 저장소 **Actions → Build Windows EXE → 성공한 실행 → Artifacts**에서 `PhotoAssistCollector-Windows`를 받습니다.
2. 압축을 모두 풉니다. 안에 ZIP이 한 번 더 있다면 그것도 풉니다.
3. `PhotoAssistCollector.exe`를 실행합니다. 옆의 `_internal` 폴더는 함께 유지합니다.
4. 폰을 USB로 연결하고 USB 디버깅/PC 연결을 허용합니다.
5. 프로그램에서 기기 찾기 → 처음 한 번 버튼 설정 → 원본 사진 선택 → 실행합니다.

Python·Node.js·Docker·Appium·Git의 별도 설치를 요구하지 않는 배포 구조입니다. PC에 따라 삼성 USB 드라이버는 필요할 수 있습니다.
인터넷은 Photo Assist 생성에 필요합니다. 프로그램은 ADB와 Python 라이브러리를 묶어 배포합니다.

**첫 실행에는 버튼 설정과 실기기 확인이 필요합니다. 현재 실제 S26+에서 전체 편집·저장 성공을 확인한 상태는 아닙니다.**
처음에는 원본 1장·스타일 1개로 시험한 뒤 전체 실행으로 확장합니다.

## 구성

| 파일 | 수정할 내용 |
|---|---|
| `desktop/app.py` | 창, 버튼 설정, 원본 선택, 진행 메시지 |
| `desktop/automation.py` | 폰 연결, UI 검색, 반복 편집, 저장 확인 |
| `desktop/tests/test_automation.py` | 중단·재개, 원본 유지, 저장 판정 테스트 |
| `desktop/build.ps1` | Windows EXE 빌드와 패키징 검사 |

일반 기능 수정은 앞의 두 Python 파일에서 합니다. 특정 폰의 버튼 정보는 코드 수정 대신 설정 화면에서 지정합니다.
기존 `appium_version/`, `no_node_version/`는 이전 CLI 구현이며 비교·참고용으로 유지합니다. 앞으로 기본 실행 경로는 `desktop/`입니다.

## 저장과 중단

- 원본은 ID로 매번 다시 엽니다. 결과를 다음 스타일의 원본으로 사용하지 않습니다.
- ‘다른 파일로 저장’ 후 원본과 같은 폴더에 새 이미지가 생겼는지 확인합니다.
- 원본 삭제·이동·강제 덮어쓰기·갤러리 강제 종료·루팅 명령을 사용하지 않습니다.
- 실패하거나 중지하면 불명확한 작업을 자동 재시도하지 않습니다.
- 중지 버튼은 이후 조작을 멈추며 이미 전송된 AI 생성 요청을 취소하지는 않습니다.
- 설정·진행 기록·오류 로그는 `%LOCALAPPDATA%\PhotoAssistCollector`에 저장합니다.
- [새 작업]은 기존 이력을 백업하고 새 실행 계획을 시작합니다. 사진은 삭제하지 않습니다.

## 현재 제한

- UI 정보에 이름·설명이 없는 요소는 버튼 설정 목록에 나타나지 않을 수 있습니다.
- 기본 스타일 화면 + 선택적인 목록 펼치기 버튼 + 가로 스와이프를 지원합니다. 세로 목록이나 스타일마다 다른 분류 경로는 추가 구현이 필요합니다.
- 사진 조회와 원본 URI 열기는 실제 펌웨어 권한에 따라 확인해야 합니다.
- 새 파일 연결은 저장 전후 사진 목록 차이를 이용합니다. 다른 이미지 생성·촬영·다운로드·동기화를 병행하면 안 됩니다.
- 원본 보존 확인은 경로·파일 크기·수정시간 기반이며 사진 내용의 해시 검사는 아닙니다.
- 화면 조회 시 uiautomator2의 기기 보조 프로그램이 배치·실행될 수 있습니다. 위험이 전혀 없다는 보장은 아닙니다.
- 미서명 개발용 EXE입니다. 조직의 실행 정책이나 USB 제한을 자동 해제하지 않습니다.

## 개발자 실행 및 빌드

Windows x64, Python 3.12 기준:

```powershell
cd desktop
python -m pip install -r requirements.txt
python app.py
```

테스트:

```powershell
python -m unittest discover -s tests -v
```

EXE 빌드:

```powershell
./build.ps1
```

또는 GitHub Actions의 Build Windows EXE를 사용합니다. `desktop/` 변경을 main에 반영하면 자동 빌드됩니다.
결과는 `desktop/dist/PhotoAssistCollector-Windows.zip`입니다. 빌드 환경의 모든 패키지 버전은 ZIP 안 `BUILD_VERSIONS.txt`에 기록합니다.
EXE 자체의 모듈·ADB·기기 자료·창 초기화 검사도 빌드에 포함하지만, 실기기 E2E 검증을 대신하지는 않습니다.

## 외부 프로젝트

- [uiautomator2](https://github.com/openatx/uiautomator2)
- [adbutils](https://github.com/openatx/adbutils)
- [PyInstaller](https://pyinstaller.org/)

관련 패키지의 라이선스와 고지는 배포 폴더 `licenses/`에 포함합니다.
