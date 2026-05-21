import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data" / "pbsg_golden_set_by_id"
TARGET_DIR = ROOT / "app" / "backend" / "data" / "pbsg_golden_set_by_id"


def main() -> None:
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Golden Set source directory not found: {SOURCE_DIR}")

    TARGET_DIR.parent.mkdir(parents=True, exist_ok=True)
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    shutil.copytree(SOURCE_DIR, TARGET_DIR, ignore=shutil.ignore_patterns("*.md5", "__pycache__"))
    print(f"Synced PBSG Golden Set JSON files to {TARGET_DIR}")


if __name__ == "__main__":
    main()
