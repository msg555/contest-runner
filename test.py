from sourcerunner.mount import TaskWorkspace
from sourcerunner.runc import RuncRunner


def main():
    foo = """
from urllib import request
try:
    request.urlopen('http://142.250.217.132', timeout=1)
    print("OKAY")
except request.URLError as err: 
    print("FAIL", err)
"""
    with TaskWorkspace("python3") as workspace:
        print(workspace.mount_dir)
        runner = RuncRunner(
            workspace.mount_dir,
            #args=["python3", "-c", "for _ in range(10): print('hinice')"],
            args=["python3", "-c", "print(sum([i for i in range(2 ** 24)]))"],
            #args=["python3", "-c", foo],
            uid=1000,
            gid=1000,
        )
        runner.run()


if __name__ == "__main__":
    main()
