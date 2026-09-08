# Enrollment bot code review

Documented and verified: 2026-09-08 (Europe/Lisbon, UTC+01:00)
Recall period: September 2026
Original implementation date: unknown; filesystem timestamps are not implementation evidence.

## Implementation update — 2026-09-08

The target workflow is now confirmed as visual schedule selection on the University of Coimbra InforEstudante portal. The active configuration contains four course mappings with primary and fallback class references.

The following changes supersede the corresponding findings in the original review below:

- Dry-run is the default execution mode. It performs navigation and class selection, skips the final Save click, and returns to the course list. The final submission click requires explicit `--mode live` selection.
- Shared interface references moved from the repository root to `assets/ui/`.
- All configuration paths now resolve relative to `bot.py`, independent of the process working directory.
- Startup validation rejects missing shared references, an empty course configuration, and missing primary class mappings before registering the global hotkey.
- Worker ownership prevents rapid stop/start toggles from launching overlapping automation workers. Worker exceptions now release runtime state.
- Course and class action controls are searched across the remainder of their detected row, with a small vertical tolerance for one-pixel label/control alignment differences, instead of only the screen's rightmost fixed-width strip.
- A visual dry-run mode opens a separate viewport with an annotated copy of the primary display. It hides before each new capture and prefers a secondary monitor when one is available.
- Pinned runtime dependencies, Python ignores, and nine mocked safety/configuration tests were added.

Verification performed after the update: Python compilation passed; all nine unit tests passed without desktop interaction; all six relocated shared references resolve; all four active mappings have primary and fallback class references; configuration loading also passed after changing the process working directory. Offline template matching against a supplied portal capture confirmed the expanded row region includes the `a-sp` action button. Live portal behavior and persisted enrollment remain unverified.

## Scope and repository reconciliation

Reviewed `bot.py`, asset filenames and PNG dimensions, editor settings, and local Git state. Current folder: `enrollment-bot`; branch: `main`; HEAD: `e058160` (`Initial commit`). Local status shows `main...origin/main`; no remote fetch was performed. Only `.gitattributes` was tracked at review start. The script, images, and `.vscode/` were untracked. No existing README, HANDOFF.md, repository AGENTS.md, dependency manifest, or automated tests were found. Empty `.github/appmod/appcat` directories provide no application configuration.

The folder contains the implementation, but the initial commit does not preserve it. No earlier checkout or transfer source was provided, so completeness against the previous folder is unverified. No code, assets, Git history, or existing settings were changed. This review adds README.md and this dated record.

## Findings

### P1: stop/start can overlap automation workers

Evidence: `bot.py:317-330`, plus the unconditional flag reset at line 315.

Stopping clears a shared Boolean without waiting for the previous thread to exit. Starting immediately creates another thread and restores the same Boolean. The original worker can resume seeing True, so both workers may click the desktop; either worker can also clear the other's state on exit. A mocked scheduler reproduced two outstanding workers after start/stop/start.

Recommended correction: retain a worker handle, use a per-run cancellation event, and refuse a new run until the prior worker has exited. Serialize lifecycle changes.

### P2: unhandled worker exceptions leave stale running state

Evidence: `bot.py:35-315`, particularly the unguarded `pyautogui.size()` at line 48 and cleanup at line 315.

Only selected image-not-found exceptions are handled. A display, screenshot, asset, or mouse-operation error can exit the thread before the flag is cleared. A mocked display failure reproduced `running=True` after the worker raised. This is recoverable by toggling stop then start, but the reported state is wrong and the first subsequent toggle does not restart work.

Recommended correction: add exception reporting and guaranteed lifecycle cleanup, coordinated with the worker ownership fix above.

### P2: failed class processing can strand subsequent courses

Evidence: `bot.py:66-88`, `154-160`, `271-290`.

If the preferred class cannot be located, or Save cannot be located after class selection, the sequence leaves the detail page without explicit recovery. With another course remaining, its enrollment-page wait can run indefinitely. Successful Save also assumes the portal returns to the list; that behavior has not been verified.

Recommended correction: bound every page wait and explicitly recover to a verified list page or terminate with an actionable failure. Do not advance from an unknown page state.

### P2: fallback is narrower than a general second-choice policy

Evidence: `bot.py:154-178`, `185-250`, `288-290`.

Fallback is nested inside the preferred class's enrollment-control `ImageNotFoundException` handler. If the primary class label is absent, the fallback is never searched. If image matching is configured to return None instead of raising, the missing control also bypasses fallback. These are concrete control-flow limitations; whether an absent primary should trigger fallback needs owner confirmation. A missing match also does not establish that the class has no places.

Recommended correction: define eligible fallback outcomes explicitly, normalize missing-match behavior, and separate recognition failure from confirmed unavailability.

### P2: Save clicks do not establish enrollment success

Evidence: `bot.py:227-236`, `279-300`.

The script logs a Save click and proceeds, without checking a confirmation or persisted class selection. The fallback path sets `segunda_opcao=True` immediately on clicking Save. A rejected submission or unchanged page can therefore be treated as a completed attempt, and may strand the next course.

Recommended correction: verify portal-specific success before recording enrollment, and report per-course outcomes such as confirmed, unavailable, recognition failure, and submission failure.

## Other limitations

- Assets are resolved against the process working directory and courses are loaded only once (`bot.py:12-23`). Launching elsewhere can silently load no courses or cause missing-file errors.
- Fixed right-edge regions and label-height crops assume a particular browser layout. The supplied label crops are large enough for the corresponding control templates, but that does not prove correct row alignment or live recognition.
- No scrolling searches for off-screen courses/classes; initial and post-selection scrolling use fixed amounts.
- Page detection uses generic full-screen markers, not a browser-specific or course-specific identity. Matching does not verify focus or a completed navigation transition.
- The 50-iteration load poll includes screenshot overhead and is not a strict five-second deadline.
- Stop is cooperative; a check followed by a move/click can still act after a stop request. Ctrl+C exits the listener without joining the daemon worker.
- No dependency manifest, lockfile, structured outcome log, or dry-run mode exists.

## Verification evidence

- Python 3.12.7: AST parsing passed without importing the script.
- A temporary in-memory harness extracted function definitions using AST; it did not execute module-level imports or hotkey registration. Mock threading reproduced overlapping outstanding workers; a mock display exception reproduced stale running state. No desktop action was performed and no harness file was retained.
- All 30 PNGs have valid PNG signatures and readable width/height headers. This is not full image decoding or visual matching validation.
- Five active course images have matching primary and fallback class filenames.
- Installed metadata: PyAutoGUI 0.9.54, keyboard 0.13.5, Pillow 12.1.1, opencv-python 4.12.0.88. Dependency imports and clean installation were not tested.
- Live enrollment, current portal UI, matching accuracy, and hotkeys remain UNVERIFIED. Static review and mocks do not establish operational success.

## Open goals and next decisions

1. Identify the target portal and its expected page after Save.
2. Confirm whether the intended policy is one pass or repeated availability monitoring, and the timing requirements around enrollment opening.
3. Confirm fallback policy, especially when the primary class cannot be recognized or is already selected.
4. Identify a disposable test page/session and the exact success signal before live validation.

The owner was asked about the target portal and one-pass versus repeated enrollment policy during the review; no answer had arrived when this record was written. These remain open requirements, not inferred commitments. Fixing application behavior is a subsequent task; this request was handled as review and documentation.
