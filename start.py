import os
import subprocess
import sys

process_type = os.getenv('PROCESS_TYPE', 'web').strip().lower()

subprocess.run(
    [sys.executable, 'manage.py', 'migrate', '--noinput'],
    check=True,
)

if process_type != 'worker':
    subprocess.run(
        [sys.executable, 'manage.py', 'collectstatic', '--noinput'],
        check=True,
    )

if process_type == 'worker':
    os.execv(sys.executable, [sys.executable, 'manage.py', 'run_monitors'])
elif process_type == 'impulse':
    os.execv(sys.executable, [sys.executable, 'manage.py', 'run_impulse'])
else:
    os.execv(
        sys.executable,
        [
            sys.executable,
            '-m',
            'daphne',
            '-b',
            '0.0.0.0',
            '-p',
            '8000',
            'config.asgi:application',
        ],
    )
