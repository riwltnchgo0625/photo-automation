$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# 개발자·빌드 서버용 실행. 사용자는 완성된 EXE 실행.
python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }

# --onedir: EXE 옆의 _internal에 Python, ADB, 자동화 자료를 포함.
# 전체 폴더를 배포하면 사용자 PC에 Python/Node/Docker 설치가 불필요.
python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
  --name PhotoAssistCollector --collect-all uiautomator2 --collect-all adbutils `
  --copy-metadata uiautomator2 --copy-metadata adbutils app.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }

# 오류 팝업 대기 방지를 위한 제한시간 내 패키징 검사.
$test = Start-Process -FilePath '.\dist\PhotoAssistCollector\PhotoAssistCollector.exe' -ArgumentList '--self-test' -PassThru
if (-not $test.WaitForExit(60000)) {
  $test.Kill()
  throw 'Packaged EXE self-test timed out'
}
if ($test.ExitCode -ne 0) { throw 'Packaged EXE self-test failed' }
Copy-Item USER_GUIDE.txt '.\dist\PhotoAssistCollector\USER_GUIDE.txt'
python licenses.py '.\dist\PhotoAssistCollector\licenses'
if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
python -m pip freeze | Out-File '.\dist\PhotoAssistCollector\BUILD_VERSIONS.txt' -Encoding utf8
Compress-Archive -Path '.\dist\PhotoAssistCollector' -DestinationPath '.\dist\PhotoAssistCollector-Windows.zip' -Force
Write-Host 'Created: desktop/dist/PhotoAssistCollector-Windows.zip'
