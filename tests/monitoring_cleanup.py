"""Release every disposable monitoring resource, retaining evidence on failure."""
from contextlib import ExitStack
import subprocess


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def cleanup_monitoring(names, processes, server, thread, temporary, *, systemd=False):
    try:
        with ExitStack() as cleanup:
            if server is not None:
                cleanup.callback(server.server_close)
                cleanup.callback(thread.join)
                cleanup.callback(server.shutdown)
            for process in processes:
                cleanup.callback(stop_process, process)
            for name in names:
                options = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                cleanup.callback(subprocess.run, ['podman', 'rm', '-f', '--ignore', name], check=True, **options)
                if systemd:
                    cleanup.callback(subprocess.run, ['systemctl', '--user', 'reset-failed', name], **options)
                    cleanup.callback(subprocess.run, ['systemctl', '--user', 'stop', name], check=True, **options)
    except Exception as error:
        error.add_note('Monitoring fixture retained at ' + temporary.name)
        raise
    temporary.cleanup()
