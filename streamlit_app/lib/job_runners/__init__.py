"""job_runners — standalone worker scripts invoked via subprocess.

Each runner reads a JSON params file (path passed as argv[1]) and writes its
artifacts under the run directory listed in `params["result_path"]`. Progress
is reported via stdout lines of the form `PROGRESS:<float>` (0..100).
"""
