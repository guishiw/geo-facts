"""允许 `python -m geo_facts` 调用命令行。"""

from geo_facts.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
