from sourcerunner.mount import TaskWorkspace
from sourcerunner.runc import RuncRunner


def main():
    foo = """
import time
time.sleep(1)
from urllib import request
request.urlopen('http://142.250.217.132', timeout=1)
print("OKAY")
"""
    with TaskWorkspace("python3") as workspace:
        runner = RuncRunner(
            workspace.mount_dir,
            #args=["python3", "-c", "for _ in range(10): print('hinice')"],
            #args=["python3", "-c", "print(sum([i for i in range(2 ** 24)]))"],
            args=["python3", "-c", foo],
            #args=["bash", "-c", "exit 1"],
            uid=1000,
            gid=1000,
        )
        print(runner.run())


if __name__ == "__main__":
    main()
