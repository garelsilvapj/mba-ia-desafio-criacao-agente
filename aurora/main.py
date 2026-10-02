import uvicorn


def run() -> None:
    uvicorn.run("aurora.api:api", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    run()
