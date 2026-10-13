"""배포에 포함된 패키지의 라이선스/NOTICE를 모은다. 런타임 코드가 아니다."""
from importlib.metadata import distributions
from pathlib import Path
import sys

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
inventory = []
for dist in distributions():
    name = dist.metadata.get('Name', 'unknown')
    inventory.append(f'{name}=={dist.version}')
    for file in dist.files or []:
        if any(word in file.name.upper() for word in ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')):
            source = Path(dist.locate_file(file))
            if source.is_file():
                target = out / name / str(file).replace('../', '').replace('..\\', '')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
(out / 'PACKAGES.txt').write_text('\n'.join(sorted(inventory)), encoding='utf-8')
