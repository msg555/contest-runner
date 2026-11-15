import json
import sys


def main():
    path = sys.argv[1]

    with open(os.path.join(base_path, "config.json"), "r") as fconfig:
        config = json.load(fconfig)

    files = {}
    sources = {}

    files["checker"] = {
        "type": "fs",
        "path": "static/checker.cpp",
    }
    files["testlib.h"] = {
        "type": "fs",
        "path": "static/testlib.h",
    }
    sources["checker"] = {
        "language": "c++",
        "file": "checker",
        "extra_files": {
            "testlib.h": "testlib.h",
        },
    }

    for solution in config["solutions"]:
        files["sol"] = {
            "type": "fs",
            "path": os.path.join(base_path, solution["file"]),
        }
        sources["sol"] = {
            "language": souces["lang"].lower(),
            "file": "sol",
        }
        break

    for test_idx, test in enumerate(config["tests"]):
        input_id = f"input_{test_idx}"
        output_id = f"output_{test_idx}"
        files[input_id] = {
            "type": "fs",
            "path": os.path.join(base_path, test["input"]),
        }
        files[output_id] = {
            "type": "fs",
            "path": os.path.join(base_path, test["output"]),
        }
        runs.append(
            {
                "source": "sol",
                "time_limit": config["time_limit_ms"] / 1000.0,
                "memory_limit": config["mem_limit_mb"] * 2**20,
                "output_limit": 2**30,
                "fd_map": {
                    0: {
                        "type": "read",
                        "file": input_id,
                    }
                },
            }
        )

    config


if __name__ == "__main__":
    main()
