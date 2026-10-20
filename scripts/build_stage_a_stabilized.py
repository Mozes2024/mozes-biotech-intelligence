"""Reproducible, migration-free Worker candidate; never uploads or deploys."""
import argparse
import io
from pathlib import Path
import subprocess
import tarfile

BASE = '19b75393b1dc3fe504a26fb7d770e2457e86cabf'
ROOT = Path(__file__).resolve().parents[1]

def build(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    raw = subprocess.check_output(['git','archive',BASE],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(destination,filter='data')
    for name in ('rollback-stage-a.patch','queue-fairness-stage-a.patch','stabilization-stage-a.patch'):
        patch = ROOT/'docs/d1'/name
        subprocess.run(['git','apply','--check',str(patch)],cwd=destination,check=True)
        subprocess.run(['git','apply',str(patch)],cwd=destination,check=True)
    return destination

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination',type=Path)
    args = parser.parse_args()
    print(build(args.destination).resolve())
