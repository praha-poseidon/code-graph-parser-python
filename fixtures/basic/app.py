from fastapi import FastAPI

app = FastAPI()


def helper(value: int) -> int:
    return value + 1


@app.get("/api/run")
def run() -> int:
    return helper(41)


boot_value = helper(1)
