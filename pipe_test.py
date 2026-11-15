import asyncio
import os
import shutil

from sourcerunner.mount import TaskWorkspace
from sourcerunner.runc import RuncRunner


async def main():
    async with TaskWorkspace("python3") as workspace:
        runner1 = RuncRunner(
            workspace.mount_dir,
            args=[
                "python3",
                "-u",
                "-c",
                "import sys, os; sys.stdout.write('hello\\n'); sys.stdout.flush(); os.close(1); sys.stderr.write(sys.stdin.read())",
            ],
            uid=1000,
            gid=1000,
        )
        runner2 = RuncRunner(
            workspace.mount_dir,
            args=[
                "python3",
                "-u",
                "-c",
                "import sys, os; sys.stdout.write('hi there\\n'); sys.stdout.flush(); os.close(1); sys.stderr.write(sys.stdin.read())",
            ],
            uid=1000,
            gid=1000,
        )

        p1 = os.pipe()
        p2 = os.pipe()
        os.set_inheritable(p1[0], True)
        os.set_inheritable(p1[1], True)
        os.set_inheritable(p2[0], True)
        os.set_inheritable(p2[1], True)
        result = await asyncio.gather(
            runner1.run(
                fd_map={0: p1[0], 1: p2[1]},
                close_fds=(p1[0], p2[1]),
            ),
            runner2.run(
                fd_map={0: p2[0], 1: p1[1]},
                close_fds=(p2[0], p1[1]),
            ),
        )

        import json

        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
