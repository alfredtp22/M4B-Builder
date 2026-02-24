try:
    from .app import main
except ImportError:
    from audiobook_builder.app import main

if __name__ == "__main__":
    main()
