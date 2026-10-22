"""만들기 실행 진입점. UI는 app.py, 폰 조작은 automation.py 공통 사용."""
from app import launch

if __name__ == '__main__':
    launch(prompt_mode=True)
