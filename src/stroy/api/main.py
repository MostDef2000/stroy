from __future__ import annotations

import uvicorn

from stroy.api.app import create_app


app = create_app()


def main() -> None:
    uvicorn.run("stroy.api.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
