"""Sample module for tests. It is never imported or executed by the loader."""


def greet(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("world"))
