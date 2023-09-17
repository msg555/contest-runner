from sourcerunner.mount import TaskWorkspace
from sourcerunner.runc import RuncRunner


def main():
    with TaskWorkspace("python3") as workspace:
        print(workspace.mount_dir)
        runner = RuncRunner(
            workspace.mount_dir,
            #args=["python3", "-c", "for _ in range(10): print('hinice')"],
            args=["id"],#args=["python3", "-c", "for _ in range(10): print('hinice')"],
            uid=1000,
            gid=1000,
        )
        runner.run()


if __name__ == "__main__":
    main()
