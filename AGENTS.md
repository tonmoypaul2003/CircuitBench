# CircuitBench desktop repository

Append a dated entry to `PROJECT-LOG.txt` after completing each desktop, shared-code, packaging, or release change. Record the affected edition, what changed and why, compatibility implications when relevant, verification and any limitations, and whether the change is local, committed, pushed, or published. Include commit IDs and release links when available. Use Asia/Dhaka (UTC+06:00).

Preserve existing entries. Append corrections and later publication events as new entries. Keep credentials and private logs out of the project log.

After each completed change, update the project log, verify the change, commit the relevant files and immediately push to the affected GitHub repository. For desktop builds, keep the final executable in build/<desktop version>/CircuitBench.exe inside the desktop repository and replace its root CircuitBench.exe with an identical copy.
Keep desktop build/ and the root CircuitBench.exe local. Do not stage or force-add these artifacts to the source repository.
