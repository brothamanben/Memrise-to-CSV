from pathlib import Path
import argparse
import csv
import re


DEFAULT_OUTPUT_NAME = "memrise_master.csv"
SKIP_FILENAMES = {
    DEFAULT_OUTPUT_NAME.lower(),
    "memrise_discovered_scenarios.csv",
    "memrise_download_review.csv",
}


def default_root():
    cwd = Path.cwd()
    downloads_dir = cwd / "memrise_downloads"

    if downloads_dir.exists() and downloads_dir.is_dir():
        return downloads_dir

    return cwd


def clean_cell(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def detect_scenario_id(path):
    for part in [path.stem, *[parent.name for parent in path.parents]]:
        match = re.search(r"(?<!\d)(\d{4,})(?!\d)", part)
        if match:
            return match.group(1)

    return ""


def looks_like_lesson_csv(path):
    if path.name.lower() in SKIP_FILENAMES:
        return False

    if path.name.lower().startswith("memrise_discovered_"):
        return False

    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])
    except Exception:
        return False

    normalized = {clean_cell(value).lower() for value in header}
    return {"front", "back"}.issubset(normalized)


def find_lesson_csvs(root, output_path):
    output_path = output_path.resolve()
    csv_paths = []

    for path in root.rglob("*.csv"):
        if path.resolve() == output_path:
            continue

        if looks_like_lesson_csv(path):
            csv_paths.append(path)

    return sorted(csv_paths, key=lambda item: str(item).lower())


def read_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {key: value for key, value in row.items() if key is not None}


def build_master_csv(root, output_path):
    csv_paths = find_lesson_csvs(root, output_path)

    all_columns = []
    rows = []

    for csv_path in csv_paths:
        relative_csv = csv_path.relative_to(root)
        lesson_folder = relative_csv.parent
        scenario_id = detect_scenario_id(csv_path)

        for row in read_rows(csv_path):
            for column in row:
                if column not in all_columns:
                    all_columns.append(column)

            rows.append(
                {
                    "Scenario ID": scenario_id,
                    "Lesson Folder": str(lesson_folder),
                    "Source CSV": str(relative_csv),
                    **row,
                }
            )

    fieldnames = ["Scenario ID", "Lesson Folder", "Source CSV"]
    fieldnames.extend(column for column in all_columns if column not in fieldnames)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return csv_paths, rows


def main():
    parser = argparse.ArgumentParser(
        description="Combine downloaded Memrise lesson CSV files into one master CSV."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root(),
        help=(
            "Folder to scan. Defaults to ./memrise_downloads if it exists, "
            "otherwise the current folder."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path. Defaults to <root>/memrise_master.csv.",
    )

    args = parser.parse_args()
    root = args.root.resolve()
    output_path = (args.output or (root / DEFAULT_OUTPUT_NAME)).resolve()

    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Root folder does not exist: {root}")

    csv_paths, rows = build_master_csv(root, output_path)

    print(f"Root scanned: {root}")
    print(f"Lesson CSV files found: {len(csv_paths)}")
    print(f"Rows written: {len(rows)}")
    print(f"Master CSV: {output_path}")

    if not csv_paths:
        print("\nNo lesson CSV files were found.")
        print("Run this from memrise_downloads, or use --root to point at that folder.")


if __name__ == "__main__":
    main()
