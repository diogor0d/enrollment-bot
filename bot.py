
import argparse
import pyautogui
import time
import os
from pathlib import Path
import keyboard
import threading

# Runtime state
running = False
automation_thread = None
state_lock = threading.Lock()
visual_viewport = None

# Keep each annotated capture visible long enough to follow manually.
VISUAL_HOLD_SECONDS = 1.0

BASE_DIR = Path(__file__).resolve().parent
CADEIRA_FOLDER = BASE_DIR / "cadeiras"
TURMA_FOLDER = BASE_DIR / "turmas"
UI_FOLDER = BASE_DIR / "assets" / "ui"
IMAGE_EXTENSIONS = {'.png'}

cadeira_images = []

INSCRICOES_PAGE_IMAGE = UI_FOLDER / "inscricoes-pagina.png"
INSCRICOES_BUTTON_IMAGE = UI_FOLDER / "inscricoes-botao.png"
PLS_PAGE_IMAGE = UI_FOLDER / "pls-page.png"
TPS_PAGE_IMAGE = UI_FOLDER / "tps-page.png"
INSCRICAO_BOX_IMAGE = UI_FOLDER / "box-inscrever.png"
GRAVAR_BUTTON_IMAGE = UI_FOLDER / "gravar-botao.png"

REQUIRED_UI_IMAGES = (
    INSCRICOES_PAGE_IMAGE,
    INSCRICOES_BUTTON_IMAGE,
    PLS_PAGE_IMAGE,
    TPS_PAGE_IMAGE,
    INSCRICAO_BOX_IMAGE,
    GRAVAR_BUTTON_IMAGE,
)


def load_and_validate_configuration():
    """Load course references and fail before automation if assets are incomplete."""
    if not CADEIRA_FOLDER.is_dir():
        raise FileNotFoundError(f"Course reference folder not found: {CADEIRA_FOLDER}")

    missing = [path for path in REQUIRED_UI_IMAGES if not path.is_file()]
    courses = sorted(
        file for file in CADEIRA_FOLDER.iterdir()
        if file.is_file() and file.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not courses:
        raise RuntimeError(f"No course references found in {CADEIRA_FOLDER}")

    for course in courses:
        primary_class = TURMA_FOLDER / course.name
        if not primary_class.is_file():
            missing.append(primary_class)

    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Required visual references are missing:\n{formatted}")

    return [str(course) for course in courses]

def preview_region(left, top, width, height, color="red"):
    """Draws a rectangle on a screenshot to preview a region."""
    from PIL import ImageDraw
    screenshot = pyautogui.screenshot()
    draw = ImageDraw.Draw(screenshot)
    rect = [left, top, left + width, top + height]
    draw.rectangle(rect, outline=color, width=3)
    screenshot.show()


def action_region_to_right(label_box, screen_width):
    """Build a row-local search region to the right of a detected label."""
    left, top, width, height = label_box
    region_left = max(left + width, 0)
    vertical_padding = 4
    return (
        region_left,
        max(top - vertical_padding, 0),
        max(screen_width - region_left, 1),
        height + (vertical_padding * 2),
    )


def class_enrollment_region(label_box, screen_width):
    """Preserve the original class-checkbox search region."""
    _, top, _, height = label_box
    offset = 220
    return (
        max(screen_width - offset, 0),
        top,
        offset,
        height,
    )


def complete_enrollment(save_box, dry_run, choice_label):
    """Click Save in live mode, or skip submission and return in dry-run mode."""
    x, y = pyautogui.center(save_box)
    pyautogui.moveTo(x, y)

    if dry_run:
        print(f"DRY RUN: Save click skipped for {choice_label}.")
        keyboard.send('browser_back')
        return

    pyautogui.click()
    print(f"LIVE: Save clicked for {choice_label}.")


def locate_reference(image, label, color, **kwargs):
    """Locate one reference and show its match in the diagnostic viewport."""
    if visual_viewport is not None:
        visual_viewport.hide()

    match = pyautogui.locateOnScreen(str(image), **kwargs)

    if match and visual_viewport is not None:
        try:
            visual_box = (
                match.left,
                match.top,
                match.width,
                match.height,
            )
        except AttributeError:
            visual_box = tuple(match)
        print(
            f"VISUAL: {label} at "
            f"{visual_box}"
        )
        visual_viewport.show(
            pyautogui.screenshot(),
            match,
            label,
            color,
        )
        time.sleep(VISUAL_HOLD_SECONDS)

    return match


def run_automation_sequence(dry_run):
    """Main automation sequence"""
    global running
    global cadeira_images
    
    print("Starting automation sequence...")
    
    # ir para o topo da pagina (home)
    if not running:
        return
    
    time.sleep(0.1)
    # Move mouse to center of screen (adjust as needed)
    screenWidth, screenHeight = pyautogui.size()
    pyautogui.moveTo(screenWidth // 2, screenHeight // 2 - 320)
    time.sleep(0.01)
    for _ in range(5):
        pyautogui.scroll(500)
        time.sleep(0.01)

    # Selecionar Cadeira (imagens na pasta cadeira no diretorio atual)
    
    print(cadeira_images)
    for cadeira in cadeira_images:
        if not running:
            print("Automation stopped before starting cadeira loop.")
            break

        print(f"\n[Tentativa de inscrição em {cadeira}...]")
        # esperar pela pagina de inscricoes
        wait_counter = 0
        while True:
            if not running:
                print("Automation stopped during inscricoes page wait.")
                break
            try:
                found = locate_reference(
                    INSCRICOES_PAGE_IMAGE,
                    "Enrollment list detected",
                    "#22c55e",
                    confidence=0.9,
                )
            except pyautogui.ImageNotFoundException:
                found = None
            if not found:
                if wait_counter % 10 == 0:
                    print("A aguardar pela pagina de inscricoes...")
                wait_counter += 1
            else:
                print("Pagina de inscricoes detectada.")
                break
            time.sleep(0.05)
            if not running:
                print("Automation stopped during inscricoes page wait (after sleep).")
                break

        if not running:
            print("Automation stopped after inscricoes page wait.")
            break

        # Find the text box for the cadeira
        text_box = None
        try:
            text_box = locate_reference(
                cadeira,
                f"Course: {os.path.basename(cadeira)}",
                "#38bdf8",
                confidence=0.9,
            )
        except pyautogui.ImageNotFoundException:
            print(f"ImageNotFoundException: {cadeira} not found on screen.")
        if not running:
            print("Automation stopped before processing text box.")
            break
        if text_box:
            screenWidth, screenHeight = pyautogui.size()
            region = action_region_to_right(text_box, screenWidth)
            region_left, region_top, region_width, region_height = region
            #preview_region(region_left, region_top, region_width, region_height, color="red")

            # Find the inscricoes-botao in the region
            button_box = None
            try:
                button_box = locate_reference(
                    INSCRICOES_BUTTON_IMAGE,
                    "Open course",
                    "#f59e0b",
                    region=region,
                    confidence=0.9,
                )
            except pyautogui.ImageNotFoundException:
                print("ImageNotFoundException: Button image not found in region.")
            if not running:
                print("Automation stopped before clicking inscricoes-botao.")
                break
            if button_box:
                x, y = pyautogui.center(button_box)
                pyautogui.moveTo(x, y)
                pyautogui.click()
                print(f"Clicked button for {cadeira}")
            else:
                print("Button image not found in region.")
                continue

            # Wait for page to load by polling for 'pls-page.png' or 'tps-page.png'
            page_loaded = False
            print("Aguardar pelo carregamento da pagina...")
            for i in range(50):  # up to 5 seconds
                if not running:
                    print("Automation stopped during page load wait.")
                    break
                found = None
                try:
                    found = locate_reference(
                        PLS_PAGE_IMAGE,
                        "Practical classes detected",
                        "#22c55e",
                        confidence=0.9,
                    )
                except pyautogui.ImageNotFoundException:
                    pass
                if not found:
                    try:
                        found = locate_reference(
                            TPS_PAGE_IMAGE,
                            "Theoretical-practical classes detected",
                            "#22c55e",
                            confidence=0.9,
                        )
                    except pyautogui.ImageNotFoundException:
                        pass
                if found:
                    page_loaded = True
                    print("Page loaded!")
                    pyautogui.moveTo(screenWidth // 2, screenHeight // 2 - 320)

                    turma_image = str(TURMA_FOLDER / os.path.basename(cadeira))
                    turma_box = None
                    if not os.path.exists(turma_image):
                        print(f"File not found: {turma_image}. Skipping turma step for {cadeira}.")
                    else:
                        try:
                            turma_box = locate_reference(
                                turma_image,
                                "Preferred class",
                                "#a78bfa",
                                confidence=0.95,
                            )
                        except pyautogui.ImageNotFoundException:
                            print(f"ImageNotFoundException: {turma_image} not found on screen.")
                    if not running:
                        print("Automation stopped before processing turma_box.")
                        break
                    if turma_box:
                        t_region = class_enrollment_region(turma_box, screenWidth)
                        t_region_left, t_region_top, t_region_width, t_region_height = t_region
                        #preview_region(t_region_left, t_region_top, t_region_width, t_region_height, color="blue")

                        inscrever_box = None
                        try:
                            inscrever_box = locate_reference(
                                INSCRICAO_BOX_IMAGE,
                                "Select preferred class",
                                "#facc15",
                                region=t_region,
                                confidence=0.9,
                            )
                        except pyautogui.ImageNotFoundException:
                            print("Caixa de inscrição não encontrada. Sem vaga? Tentando segunda opção...")
                            # Try turma_image+'-2' if it exists
                            turma_image_root, turma_image_ext = os.path.splitext(turma_image)
                            turma_image_2 = turma_image_root + '-2' + turma_image_ext
                            
                            segunda_opcao = False
                            
                            if os.path.exists(turma_image_2):
                                try:
                                    turma_box_2 = locate_reference(
                                        turma_image_2,
                                        "Fallback class",
                                        "#c084fc",
                                        confidence=0.95,
                                    )
                                except pyautogui.ImageNotFoundException:
                                    turma_box_2 = None
                                if turma_box_2:
                                    t2_region = class_enrollment_region(
                                        turma_box_2,
                                        screenWidth,
                                    )
                                    t2_region_left, t2_region_top, t2_region_width, t2_region_height = t2_region
                                    try:
                                        inscrever_box_2 = locate_reference(
                                            INSCRICAO_BOX_IMAGE,
                                            "Select fallback class",
                                            "#facc15",
                                            region=t2_region,
                                            confidence=0.9,
                                        )
                                    except pyautogui.ImageNotFoundException:
                                        inscrever_box_2 = None
                                    if inscrever_box_2:
                                        x, y = pyautogui.center(inscrever_box_2)
                                        pyautogui.moveTo(x, y)
                                        
                                        
                                        
                                        pyautogui.click()
                                        print(f"Clicked inscrever for {cadeira} (segunda opção)")
                                        pyautogui.moveTo(screenWidth // 2, screenHeight // 2)
                                        for _ in range(5):
                                            if not running:
                                                print("Automation stopped during scroll after inscrever segunda opção.")
                                                break
                                            pyautogui.scroll(-500)
                                            time.sleep(0.01)
                                        if not running:
                                            print("Automation stopped before locating gravar-botao (segunda opção).")
                                            break
                                        gravar_box_2 = None
                                        try:
                                            gravar_box_2 = locate_reference(
                                                GRAVAR_BUTTON_IMAGE,
                                                "Final Save target",
                                                "#fb7185",
                                                confidence=0.9,
                                            )
                                        except pyautogui.ImageNotFoundException:
                                            print("ImageNotFoundException: gravar-botao.png not found on screen (segunda opção).")
                                        if not running:
                                            print("Automation stopped before clicking gravar-botao (segunda opção).")
                                            break
                                        if gravar_box_2:
                                            complete_enrollment(
                                                gravar_box_2,
                                                dry_run,
                                                f"{os.path.basename(cadeira)} (second choice)",
                                            )
                                            segunda_opcao = True
                                        else:
                                            print("gravar-botao.png not found on screen (segunda opção).")
                                    else:
                                        print("Segunda opção: box-inscrever não encontrada.")
                                else:
                                    print(f"Segunda opção: {turma_image_2} not found on screen.")
                            else:
                                print(f"Segunda opção: {turma_image_2} file does not exist.")
                                
                            
                            if not segunda_opcao:
                                print("No available slots found in either option.")
                                keyboard.send('browser_back')
                            print("Prosseguindo para próxima cadeira...")
                        if not running:
                            print("Automation stopped before clicking inscrever_box.")
                            break
                        if inscrever_box:
                            x, y = pyautogui.center(inscrever_box)
                            pyautogui.moveTo(x, y)
                            pyautogui.click()
                            print(f"Clicked inscrever for {cadeira}")

                            pyautogui.moveTo(screenWidth // 2, screenHeight // 2)
                            for _ in range(5):
                                if not running:
                                    print("Automation stopped during scroll after inscrever.")
                                    break
                                pyautogui.scroll(-500)
                                time.sleep(0.01)

                            if not running:
                                print("Automation stopped before locating gravar-botao.")
                                break
                            gravar_box = None
                            try:
                                gravar_box = locate_reference(
                                    GRAVAR_BUTTON_IMAGE,
                                    "Final Save target",
                                    "#fb7185",
                                    confidence=0.9,
                                )
                            except pyautogui.ImageNotFoundException:
                                print("ImageNotFoundException: gravar-botao.png not found on screen.")
                            if not running:
                                print("Automation stopped before clicking gravar-botao.")
                                break
                            if gravar_box:
                                complete_enrollment(
                                    gravar_box,
                                    dry_run,
                                    f"{os.path.basename(cadeira)} (preferred choice)",
                                )
                            else:
                                print("gravar-botao.png not found on screen.")
                    else:
                        print(f"Turma image {turma_image} not found on screen.")
                    break
                if i % 10 == 0:
                    print("Pagina de turmas não encontrada.")
                time.sleep(0.1)
            if not running:
                print("Automation stopped after page load wait.")
                break
            if not page_loaded:
                print("Page did not load within timeout.")

            print(f"Tentativa de inscrição para {cadeira} dada como terminada.")
        else:
            print(f"Image {cadeira} not found on screen.")
        if not running:
            print("Automation stopped after processing cadeira.")
            break

    print("Automation sequence completed or stopped.")


def automation_sequence(dry_run):
    """Run one worker and always release its runtime state."""
    global running
    global automation_thread

    try:
        run_automation_sequence(dry_run)
    except Exception as error:
        print(f"Automation failed: {error}")
    finally:
        with state_lock:
            if threading.current_thread() is automation_thread:
                running = False
                automation_thread = None


def start_automation(dry_run):
    """Start the automation sequence unless a previous worker is still exiting."""
    global running
    global automation_thread

    with state_lock:
        if automation_thread is not None and automation_thread.is_alive():
            print("The previous automation run is still stopping.")
            return

        running = True
        automation_thread = threading.Thread(
            target=automation_sequence,
            args=(dry_run,),
            daemon=True,
        )
        automation_thread.start()

def stop_automation():
    """Request a cooperative stop."""
    global running

    with state_lock:
        running = False
    print("Stopping automation...")


def capslock_toggle(dry_run):
    with state_lock:
        is_running = running

    if is_running:
        stop_automation()
    else:
        start_automation(dry_run)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Visual schedule-selection automation for InforEstudante."
    )
    parser.add_argument(
        "--mode",
        choices=("dry-run", "visual", "live"),
        default="dry-run",
        help=(
            "dry-run skips Save; visual adds a diagnostic viewport to dry-run; "
            "live enables submission"
        ),
    )
    return parser.parse_args(argv)


def main(argv=None):
    global cadeira_images
    global visual_viewport

    args = parse_args(argv)
    dry_run = args.mode != "live"
    visual_mode = args.mode == "visual"

    try:
        cadeira_images = load_and_validate_configuration()
    except (FileNotFoundError, RuntimeError) as error:
        print(f"Configuration error: {error}")
        return 2

    if visual_mode:
        try:
            from ui.viewport import LiveViewport

            screen_width, screen_height = pyautogui.size()
            visual_viewport = LiveViewport(screen_width, screen_height)
            visual_viewport.start()
            if visual_viewport.last_error:
                raise RuntimeError(visual_viewport.last_error)
        except Exception as error:
            if visual_viewport is not None:
                visual_viewport.close()
            visual_viewport = None
            print(f"Visual viewport unavailable: {error}")
            print("Continuing as a normal dry-run.")

    mode_message = {
        "dry-run": "DRY RUN — final Save clicks are disabled.",
        "visual": "VISUAL DRY RUN — detections appear in a viewport; Save is disabled.",
        "live": "LIVE — final Save clicks are enabled.",
    }[args.mode]
    if visual_mode and visual_viewport is None:
        mode_message = "DRY RUN — visual viewport unavailable; Save is disabled."
    print(f"Loaded {len(cadeira_images)} course references.")
    print(mode_message)
    print("Press Caps Lock to START/STOP automation")
    print("Press Ctrl+C to exit the script")

    hotkey = keyboard.add_hotkey('caps lock', lambda: capslock_toggle(dry_run))
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        print("\nScript terminated.")
    finally:
        stop_automation()
        keyboard.remove_hotkey(hotkey)
        if visual_viewport is not None:
            visual_viewport.close()
            visual_viewport = None

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
