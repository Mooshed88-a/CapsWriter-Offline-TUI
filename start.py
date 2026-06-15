# coding: utf-8
from multiprocessing import freeze_support


if __name__ == "__main__":
    freeze_support()
    try:
        from core.single_app.app import main
    except ModuleNotFoundError as exc:
        missing = exc.name or str(exc)
        print(f"缺少运行依赖：{missing}")
        print("请先安装依赖：pip install -r requirements.txt")
        raise SystemExit(1)
    main()
