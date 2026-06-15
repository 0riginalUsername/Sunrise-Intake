# Data Intake Application

A PyQt5-based desktop application for processing drone survey data, including sensor detection, folder structure creation, RINEX conversion, and REDToolBox PPK processing.

## Version

- **Current Version**: 2.1.1
- **Build Date**: 06/15/2026

See [CHANGELOG.md](CHANGELOG.md) for release history. Older source revisions are kept under `_archive/`.

## Repository Layout

```
src/            Application source — data_intake.py is the single source of truth
tools/          Standalone helper scripts (PPP, base comparison, etc.)
docs/           Reference notes
_archive/       Previous versions of the app, kept for reference
build.py        Build + version + publish (see "Building a Release")
build_config.ini  Build settings (exe name, icon, current version)
```

> Build outputs (`build/`, `dist/`, `*.exe`, `*.spec`) and large `sample-data/` are intentionally not tracked in git.

## Features

- **Automatic Sensor Detection**: Reads EXIF metadata from drone images to detect sensor type
- **Folder Structure Creation**: Automatically creates organized project folders based on sensor type
- **Base Data Processing**: Handles Trimble T02/T04 files and RINEX conversions
- **PPK Processing**: Integrates with REDToolBox CLI for PPK corrections (L2, P1, M3E, R3Pro)
- **Multi-file Support**: Drag-and-drop support for multiple base data files and source folders
- **Progress Tracking**: Real-time progress bars and status updates

## Supported Sensors

| EXIF Model | Sensor Type | RTB Processing |
|------------|-------------|----------------|
| `PMA2616`  | R3Pro       | ✅ Yes         |
| `L2`       | L2          | ✅ Yes         |
| `L3`       | L3          | ❌ No (use DJI Terra) |
| `M3E`      | M3E         | ✅ Yes         |
| `ZenmuseP1`| P1          | ✅ Yes         |

> **Note**: L3 sensor data should be processed in DJI Terra. The application will skip RTB processing for L3.

## Requirements

### Python Dependencies

```
PyQt5>=5.15.0
Pillow>=9.0.0
```

### External Tools

- **Trimble convertToRINEX**: `C:\Program Files (x86)\Trimble\convertToRINEX\convertToRinex.exe`
- **REDToolBox CLI**: `C:\Program Files\REDToolBox\resources\assets\REDToolBoxCLI\REDToolBoxCLI.exe`

## Installation

1. Ensure Python 3.8+ is installed
2. Install dependencies:
   ```bash
   pip install PyQt5 Pillow
   ```
3. Install external tools (Trimble convertToRINEX, REDToolBox)
4. Run the application:
   ```bash
   python src/data_intake.py
   ```

## Building a Release

Run `build.py` (or double-click `build_exe.bat`). It will:

1. Prompt for version, publish date, and a one-line release note.
2. Patch the version shown in the app UI.
3. Build the `.exe` into `dist/` via PyInstaller.
4. Append the note to `CHANGELOG.md`.
5. Offer to commit, tag the release (`vX.Y.Z`), and push to GitHub.

```bash
py build.py
```

To check out an older release later: `git checkout v2.0` (or download the zip from the GitHub *Tags* page).

## Usage

1. **Select Output Folder**: Choose the base 3dData directory
2. **Add Base Data**: Drag-and-drop or select T02/T04 or RINEX files
3. **Add Source Data**: Drag-and-drop drone data folders
4. **Enter Project Info**: Fill in Client and Project names
5. **Start Processing**: Click "Start data intake processes"

## Project Structure

The application creates the following folder structure:

### LiDAR Sensors (L2, L3)
```
Client/
└── Project/
    └── DDMmmYYYY/
        ├── BaseData/
        ├── L2/ or L3/
        ├── PPK/
        │   └── BaseData/
        ├── Pix4d/
        ├── Terra/
        └── TerraArchive/
```

### Standard Sensors (M3E, P1, R3Pro)
```
Client/
└── Project/
    └── DDMmmYYYY/
        ├── BaseData/
        ├── [Sensor]/
        └── Pix4D/
```

## Architecture (Modularized Version)

The refactored codebase follows **DRY** (Don't Repeat Yourself) and **SRP** (Single Responsibility Principle):

### Modules

| Module | Purpose |
|--------|---------|
| `Config` | Application constants, paths, and sensor mappings |
| `Styles` | Centralized UI styling |
| `FileOperations` | File/folder manipulation utilities |
| `SensorDetector` | EXIF-based sensor detection |
| `FolderStructureBuilder` | Sensor-specific folder creation |
| `RinexProcessor` | RINEX conversion and file handling |
| `RTBProcessor` | REDToolBox CLI integration |
| `ProcessingWorker` | Background processing orchestration |
| `DataIntakeUI` | User interface |

### Key Improvements from Original

1. **Eliminated Redundancy**:
   - Merged identical `L2_rename` and `L3_rename` into `RinexProcessor.rename_for_sensor()`
   - Unified folder structure templates
   - Removed duplicate code at file end

2. **Single Responsibility**:
   - Each class handles one concern
   - UI separated from business logic
   - File operations centralized

3. **Configuration Centralization**:
   - All paths, constants in `Config` class
   - All styles in `Styles` class
   - Sensor mappings in one place

## Logging

Logs are saved to:
```
[OutputFolder]/[Client]/[Project]/[Date]/[Client]_[Project]_[Date]_intake.log
```

## Configuration

Key configuration options in `Config` class:

```python
# Paths
LAST_FOLDER_FILE = "AppData/DataIntake_last_folder.txt"
ATX_REPO_DIR = "Z:\\Survey\\UT\\_Scripts\\GMS\\_ATX-REPO"

# Processing
SUBPROCESS_TIMEOUT = 1800  # 30 minutes
COPY_BUFFER_SIZE = 4 * 1024 * 1024  # 4 MB

# Sensor mappings
EXIF_MODEL_TO_SENSOR = {
    "PMA2616": "R3Pro",
    "L2": "L2",
    "L3": "L3",
    "M3E": "M3E",
    "ZenmuseP1": "P1",
}
```

## Troubleshooting

### "Sensor not detected" Error

The EXIF Model tag from your images doesn't match a known sensor. The error message will show:
- The EXIF Model value found
- Supported models list
- Image path checked

**Solution**: Add the new EXIF model to `Config.EXIF_MODEL_TO_SENSOR`

### PIL DecompressionBombWarning

Large images (100+ megapixels like L3) trigger this warning. It's disabled by default:
```python
Image.MAX_IMAGE_PIXELS = None
```

### RTB "device not found" Error

REDToolBox CLI only supports specific devices:
- `dji` (generic)
- `dji_l2` (used for both L2 and L3)
- `imagevector`, `ricoh`, `autel`, `swift_nav`, `emlidm2`, `ublox`, `yuneec`, `septentrio`

## License

Internal use only - Sunrise Engineering

## Support

Contact the Survey/GIS team for support.
