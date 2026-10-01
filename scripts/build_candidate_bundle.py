"""Mechanically rebuild the immutable, checked-in source/case bundle."""
import json

from mozes.historical_batch import BUNDLE, DATA, build_bundle


if __name__ == "__main__":
    path = DATA / BUNDLE
    path.write_text(json.dumps(build_bundle(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)
