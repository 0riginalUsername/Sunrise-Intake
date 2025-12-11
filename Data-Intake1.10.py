from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QComboBox, QLineEdit,
    QVBoxLayout, QHBoxLayout, QFileDialog, QMessageBox, QScrollArea, QSizePolicy, QProgressBar
)
from PyQt5.QtGui import QFont, QPalette, QColor, QIcon
from PyQt5.QtCore import Qt, QMimeData, QUrl, QThread, pyqtSignal, QObject
from PyQt5.QtMultimedia import QSound
import sys
import os
import shutil
import subprocess
import glob
import logging
import urllib.request

sensor_list = ["L2", "P1", "M3E", "R3Pro"]

# Timeout constant for subprocess calls (in seconds)
SUBPROCESS_TIMEOUT = 1800

logger = logging.getLogger("data_intake")

# Path to store last selected folder between sessions
LAST_FOLDER_FILE = os.path.join(os.environ.get("APPDATA", os.getcwd()), "DataIntake_last_folder.txt")
# ATX repository path
ATX_REPO_DIR = r"Z:\Survey\UT\_Scripts\GMS\_ATX-REPO"


class StreamToLogger:
    """Redirects stdout/stderr to the configured logger."""

    def __init__(self, logger_instance, level=logging.INFO):
        self.logger = logger_instance
        self.level = level

    def write(self, message):
        message = message.strip()
        if message:
            for line in message.splitlines():
                self.logger.log(self.level, line.strip())

    def flush(self):
        pass


def configure_logging(log_file_path):
    """Configure logging to write detailed steps and errors to a file."""
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Redirect stdout/stderr so existing print statements are captured in the log file.
    sys.stdout = StreamToLogger(logger, logging.INFO)
    sys.stderr = StreamToLogger(logger, logging.ERROR)

    logger.info(f"Logging initialized -> {log_file_path}")

class ProcessingWorker(QThread):
    # Signals for communication with the main thread
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    file_copy_progress = pyqtSignal(int, int, str)  # current, total, folder_name
    error_occurred = pyqtSignal(str)
    processing_complete = pyqtSignal(str, str, str, str, object, int, str)  # CLIENT, PROJECT, DATE_CURR, sensor_folder_path, first_image_found, files_copied, date_folder_path
    
    def __init__(self, selected_folder, data_source_folders, base_data_paths, client, project, sensor_choice, base_data_is_rinex=False):
        super().__init__()
        self.selected_folder = selected_folder
        self.data_source_folders = data_source_folders
        self.base_data_paths = base_data_paths
        self.client = client
        self.project = project
        self.sensor_choice = sensor_choice
        self.base_data_is_rinex = base_data_is_rinex
        self.should_stop = False
        self.logger = logging.getLogger("data_intake")
        
    def stop_processing(self):
        self.should_stop = True
        
    def run(self):
        try:
            self.logger.info("Processing thread started")
            self.process_data()
        except Exception as e:
            self.logger.exception("Critical error during processing thread run")
            self.error_occurred.emit(f"Critical error during processing: {str(e)}")

    def process_data(self):
        try:
            # Emit status update
            self.status_update.emit("Starting data processing...")
            
            # Find first image and get date
            image_date_folder = None
            first_image_found = None
            
            if self.data_source_folders:
                for folder in self.data_source_folders:
                    if self.should_stop:
                        return
                    first_image = self.find_first_image(folder)
                    if first_image:
                        first_image_found = first_image
                        image_date_folder = self.get_image_date_taken(first_image)
                        if image_date_folder:
                            break
            
            if not image_date_folder:
                image_date_folder = datetime.now().strftime("%d%b%Y")
            
            DATE_CURR = image_date_folder
            
            # Configure logging now that DATE_CURR is known; place logs in client/project/DATE_CURR
            log_dir = os.path.join(self.selected_folder, self.client, self.project, DATE_CURR)
            os.makedirs(log_dir, exist_ok=True)
            self.log_file_path = os.path.join(log_dir, f"{self.client}_{self.project}_{DATE_CURR}_intake.log")
            configure_logging(self.log_file_path)
            logger.info("----- New data intake run started -----")
            logger.info(f"Client: {self.client}, Project: {self.project}, Sensor: {self.sensor_choice}")
            logger.info(f"Data sources: {self.data_source_folders}")
            logger.info(f"Base data files ({len(self.base_data_paths)}): {self.base_data_paths} (RINEX provided: {self.base_data_is_rinex})")
            self.logger.info("Starting data processing")
            
            # Create folder structure
            self.status_update.emit("Creating folder structure...")
            
            if self.sensor_choice == "M3E":
                folder_structure = {
                    self.client: {self.project: {DATE_CURR: {self.sensor_choice: {}, "BaseData": {}, "Pix4D": {}}}}
                }
            elif self.sensor_choice == "P1":
                folder_structure = {
                    self.client: {self.project: {DATE_CURR: {self.sensor_choice: {}, "BaseData": {}, "Pix4D": {}}}}
                }
            elif self.sensor_choice == "L2":
                folder_structure = {
                    self.client: {
                        self.project: {
                            DATE_CURR: {
                                "BaseData": {},
                                self.sensor_choice: {},
                                "PPK": {"BaseData": {}},
                                "Pix4d": {},
                                "TerraArchive": {},
                                "Terra": {},
                            }
                        }
                    }
                }
            elif self.sensor_choice == "R3Pro":
                folder_structure = {
                    self.client: {self.project: {DATE_CURR: {self.sensor_choice: {}, "BaseData": {}, "Pix4D": {}}}}
                }
            
            self.create_folder_structure(self.selected_folder, folder_structure)
            
            sensor_folder_path = os.path.join(self.selected_folder, self.client, self.project, DATE_CURR, self.sensor_choice)
            
            # Copy data source files
            self.status_update.emit("Copying data source files...")
            files_copied = self.copy_data_source_to_choice(sensor_folder_path, self.data_source_folders)
            
            if self.should_stop:
                return
                
            # Handle sensor-specific processing
            if self.sensor_choice == "L2":
                base_data_targets = [
                    os.path.join(self.selected_folder, self.client, self.project, DATE_CURR, "BaseData")
                ]
                self.copy_base_data(self.base_data_paths, base_data_targets)
                
                date_folder_path = os.path.join(self.selected_folder, self.client, self.project, DATE_CURR)
                ppk_folder_path = os.path.join(self.selected_folder, self.client, self.project, DATE_CURR, "PPK")
                
                self.copy_choice_to_ppk(sensor_folder_path, ppk_folder_path)
                self.cleanup_ppk_folder(ppk_folder_path, date_folder_path)
            elif self.sensor_choice == "R3Pro":
                # R3Pro: Run convertToRinex once on base data in permanent BaseData folder, then copy converted files to each subfolder's POS/base
                self.status_update.emit("Processing R3Pro base data...")
                
                # Use the permanent BaseData folder
                base_folder = os.path.join(self.selected_folder, self.client, self.project, DATE_CURR, "BaseData")
                os.makedirs(base_folder, exist_ok=True)
                
                # Copy base data to permanent BaseData folder and run convertToRinex once
                base_data_targets = [base_folder]
                self.copy_base_data(self.base_data_paths, base_data_targets)
                if self.base_data_is_rinex:
                    self.status_update.emit("RINEX provided; skipping conversion.")
                    self.rename_mix_to_nav(base_folder)
                else:
                    self.batch_convert_to_rinex(base_folder)
                
                # Copy converted files to each R3Pro subfolder's POS/base
                for subfolder_name in os.listdir(sensor_folder_path):
                    subfolder_path = os.path.join(sensor_folder_path, subfolder_name)
                    if os.path.isdir(subfolder_path):
                        # Create POS/base folder within each R3Pro subfolder
                        target_base_folder = os.path.join(subfolder_path, "POS", "base")
                        os.makedirs(target_base_folder, exist_ok=True)
                        
                        # Copy all files from permanent BaseData folder to this subfolder's POS/base
                        for file_name in os.listdir(base_folder):
                            source_file = os.path.join(base_folder, file_name)
                            dest_file = os.path.join(target_base_folder, file_name)
                            if os.path.isfile(source_file):
                                shutil.copy2(source_file, dest_file)
                
                self.status_update.emit("R3Pro processing completed")
            else:
                base_folder = os.path.join(self.selected_folder, self.client, self.project, DATE_CURR, "BaseData")
                base_data_targets = [base_folder]
                self.copy_base_data(self.base_data_paths, base_data_targets)
                if self.base_data_is_rinex:
                    self.status_update.emit("RINEX provided; skipping conversion.")
                    self.rename_mix_to_nav(base_folder)
                else:
                    self.batch_convert_to_rinex(base_folder)
                
                # Run RTB processing for non-L2 sensors
                self.status_update.emit("Running RTB processing...")
                self.run_rtb(sensor_folder_path, base_folder, None)
            
            # Emit completion signal
            date_folder_path = os.path.join(self.selected_folder, self.client, self.project, DATE_CURR)
            self.processing_complete.emit(self.client, self.project, DATE_CURR, sensor_folder_path, first_image_found, files_copied, date_folder_path)
            self.logger.info("Processing complete")
            
        except Exception as e:
            self.logger.exception("Error during processing")
            self.error_occurred.emit(f"Error during processing: {str(e)}")
    
    def create_folder_structure(self, base_path, structure):
        """Create nested folder structure"""
        for name, subdict in structure.items():
            path = os.path.join(base_path, name)
            os.makedirs(path, exist_ok=True)
            if subdict:
                self.create_folder_structure(path, subdict)
    
    def find_first_image(self, folder):
        """Find the first image in a folder"""
        for root_dir, _, files in os.walk(folder):
            image_files = sorted(
                f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png"))
            )
            if image_files:
                image_path = os.path.join(root_dir, image_files[0])
                return image_path
        return None
    
    def get_image_date_taken(self, image_path):
        """Extract date from image EXIF data"""
        try:
            image = Image.open(image_path)
            exif_data = image._getexif()
            if exif_data:
                for tag_id, value in exif_data.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag == "DateTimeOriginal":
                        date_str = value.split(" ")[0]
                        year, month, day = date_str.split(":")
                        month_name = datetime.strptime(month, "%m").strftime("%b")
                        return f"{day}{month_name}{year}"
        except Exception as e:
            print(f"Error reading EXIF date from {image_path}: {e}")
        return None
    
    def copy_data_source_to_choice(self, sensor_folder_path, data_source_folders):
        """Copy files from data source folders to sensor folder in batches"""
        files_copied = 0
        total_files_to_copy = sum(
            len(files)
            for source_folder in data_source_folders
            for _, _, files in os.walk(source_folder)
        )
        
        current_file = 0
        for source_folder in data_source_folders:
            if self.should_stop:
                break
                
            source_folder_name = os.path.basename(source_folder)
            
            # First, create all directories (including empty ones)
            for root_dir, dirs, files in os.walk(source_folder):
                rel_path = os.path.relpath(root_dir, source_folder)
                target_folder = os.path.join(sensor_folder_path, source_folder_name, rel_path)
                os.makedirs(target_folder, exist_ok=True)
            
            # Then copy all files
            for root_dir, _, files in os.walk(source_folder):
                for file in files:
                    if self.should_stop:
                        break
                        
                    source_file = os.path.join(root_dir, file)
                    rel_path = os.path.relpath(root_dir, source_folder)
                    target_folder = os.path.join(sensor_folder_path, source_folder_name, rel_path)
                    dest_file = os.path.join(target_folder, file)
                    
                    # Handle duplicate filenames
                    counter = 1
                    base_name, ext = os.path.splitext(file)
                    while os.path.exists(dest_file):
                        dest_file = os.path.join(target_folder, f"{base_name}_{counter}{ext}")
                        counter += 1
                    
                    try:
                        shutil.copy2(source_file, dest_file)
                        files_copied += 1
                        current_file += 1
                        
                        # Emit progress update every 25 files to reduce UI overhead
                        if current_file % 25 == 0 or current_file == total_files_to_copy:
                            self.file_copy_progress.emit(current_file, total_files_to_copy, source_folder_name)
                    except Exception as e:
                        print(f"Failed to copy {source_file}: {e}")
        
        return files_copied
    
    def collect_rinex_files(self, base_data_path):
        """Return a list of RINEX companion files to copy when base data is pre-converted."""
        base_dir = os.path.dirname(base_data_path)
        base_prefix = os.path.splitext(os.path.basename(base_data_path))[0]
        rinex_suffixes = ("o", "n", "g", "p", "l", "s", "obs", "rnx", "crx", "mix", "nav")
        files_to_copy = []
        try:
            for file in os.listdir(base_dir):
                if not file.startswith(base_prefix):
                    continue
                lower_name = file.lower()
                if lower_name.endswith(rinex_suffixes):
                    files_to_copy.append(os.path.join(base_dir, file))
        except FileNotFoundError:
            pass
        return files_to_copy or [base_data_path]

    def rename_mix_to_nav(self, folder_path):
        """Rename any *.mix/*.*mix files to .n in the provided folder."""
        try:
            mix_files = glob.glob(os.path.join(folder_path, "*.*mix")) + glob.glob(os.path.join(folder_path, "*.mix"))
            for mix_file in mix_files:
                if not mix_file.lower().endswith("mix"):
                    continue
                nav_file = mix_file[:-3] + "nav"  # replace trailing 'mix' with 'nav' while retaining year suffix
                if os.path.exists(nav_file):
                    continue
                os.rename(mix_file, nav_file)
                print(f"Renamed {os.path.basename(mix_file)} to {os.path.basename(nav_file)}")
        except Exception as e:
            print(f"Failed to normalize RINEX nav file names in {folder_path}: {e}")

    def copy_base_data(self, base_data_paths, base_data_targets):
        """Copy one or more base data files to target locations."""
        if not base_data_paths:
            self.logger.error("No base data files provided to copy.")
            return

        valid_sources = [p for p in base_data_paths if os.path.isfile(p)]
        if not valid_sources:
            self.logger.error("No valid base data files found to copy.")
            return

        for target_folder in base_data_targets:
            if self.should_stop:
                break

            os.makedirs(target_folder, exist_ok=True)

            for source_path in valid_sources:
                files_to_copy = self.collect_rinex_files(source_path) if self.base_data_is_rinex else [source_path]
                for file_path in files_to_copy:
                    file_name = os.path.basename(file_path)
                    dest_file = os.path.join(target_folder, file_name)
                    try:
                        shutil.copy2(file_path, dest_file)
                    except Exception as e:
                        print(f"Failed to copy base data to {dest_file}: {e}")

            if self.base_data_is_rinex:
                self.rename_mix_to_nav(target_folder)
    
    def copy_choice_to_ppk(self, sensor_folder_path, ppk_folder_path):
        """Copy sensor folder contents to PPK folder"""
        for item in os.listdir(sensor_folder_path):
            if self.should_stop:
                break
                
            src_path = os.path.join(sensor_folder_path, item)
            dest_path = os.path.join(ppk_folder_path, item)
            if os.path.isdir(src_path):
                if os.path.exists(dest_path):
                    shutil.rmtree(dest_path)
                try:
                    shutil.copytree(src_path, dest_path)
                except Exception as e:
                    print(f"Failed to copy folder {src_path}: {e}")
    
    def cleanup_ppk_folder(self, ppk_folder_path, date_folder_path):
        """Clean up PPK folder and run conversions"""
        # Clean unwanted files
        unwanted_exts = {".LDR", ".DBG", ".LDRT"}
        for root_dir, _, files in os.walk(ppk_folder_path):
            for file in files:
                if self.should_stop:
                    break
                    
                file_ext = os.path.splitext(file)[1].upper()
                if file_ext in unwanted_exts:
                    file_path = os.path.join(root_dir, file)
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        print(f"Failed to delete {file_path}: {e}")
        
        # Run RINEX conversions once (in BaseData) unless RINEX was already provided, then copy results to PPK/BaseData
        base_data_date_folder = os.path.join(date_folder_path, "BaseData")
        base_data_ppk_folder = os.path.join(ppk_folder_path, "BaseData")
        os.makedirs(base_data_ppk_folder, exist_ok=True)
        if self.base_data_is_rinex:
            self.status_update.emit("RINEX provided; skipping conversion.")
            self.rename_mix_to_nav(base_data_date_folder)
        else:
            self.status_update.emit("Converting to RINEX format...")
            self.batch_convert_to_rinex(base_data_date_folder)

        # Sync converted/provided files to PPK/BaseData
        try:
            # Clear PPK BaseData before copy
            for f in os.listdir(base_data_ppk_folder):
                fp = os.path.join(base_data_ppk_folder, f)
                if os.path.isfile(fp):
                    os.remove(fp)
                else:
                    shutil.rmtree(fp, ignore_errors=True)
            for f in os.listdir(base_data_date_folder):
                src = os.path.join(base_data_date_folder, f)
                dst = os.path.join(base_data_ppk_folder, f)
                try:
                    shutil.copy2(src, dst)
                except Exception as e:
                    print(f"Failed to copy converted file to PPK BaseData: {e}")
        except Exception as e:
            print(f"Failed to sync BaseData to PPK BaseData: {e}")
        
        # Find RINEX file and run L2_rename and RTB processing for L2 sensors
        rinex_folder = base_data_ppk_folder
        rinex_file_path = None
        for root_dir, _, files in os.walk(rinex_folder):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                norm_ext = ext
                if len(norm_ext) > 3 and norm_ext[1:3].isdigit():
                    norm_ext = "." + norm_ext[3:]
                if norm_ext in (".o", ".obs"):
                    rinex_file_path = os.path.join(root_dir, file)
                    print(f"✅ Found RINEX file: {rinex_file_path}")
                    break
            if rinex_file_path:
                break
                
        if rinex_file_path:
            print(f"🚀 Calling L2_rename with file: {rinex_file_path}")
            self.L2_rename(rinex_file_path, os.path.join(date_folder_path, "L2"))
            print("🚀 Running RTB processing after RINEX conversion...")
            image_dir = os.path.join(date_folder_path, "PPK")
            base_dir = os.path.join(ppk_folder_path, "BaseData")
            self.status_update.emit("Running RTB processing for L2...")
            self.run_rtb(image_dir, base_dir, ppk_folder_path)
        else:
            print("⚠️ No RINEX .??o file found — skipping L2_rename and RTB.")
    
    def batch_convert_to_rinex(self, folder_path):
        print(f"Starting RINEX conversion in folder: {folder_path}")

        if not os.path.isdir(folder_path):
            print("Error: The provided folder path does not exist.")
            return
        t02_t04_files = [f for f in os.listdir(folder_path) if f.endswith((".T02", ".T04", ".t02", ".t04", "T0B"))]
        print(f"Found T02/T04 files: {t02_t04_files}")
        if not t02_t04_files:
            print("No .T02 or .T04 files found in the specified folder.")
            return
        convert_to_rinex_exe = "C:\\Program Files (x86)\\Trimble\\convertToRINEX\\convertToRinex.exe"
        if not os.path.isfile(convert_to_rinex_exe):
            print(f"Error: {convert_to_rinex_exe} not found.")
            return
        for file_name in t02_t04_files:
            try:
                file_path = os.path.join(folder_path, file_name)
                print(f"Processing file: {file_path}")
                rinex_version = "-v"
                rinex_version_value = "3.05"
                command = [convert_to_rinex_exe, file_path, rinex_version, rinex_version_value]
                print(f"Running command: {' '.join(command)}")
                subprocess.run(command, check=True, timeout=1800)
                self.rename_mix_to_nav(folder_path)
                print(f"Successfully converted {file_name} to RINEX.")
            except Exception as e:
                print(f"Error converting {file_name}: {e}")
            
    def copy_rtb_images_to_exif_c(self, image_dir, ppk_folder, entries):
        """Copy all JPGs from RTB output directories into exif_C folder with progress tracking"""
        # Create exif_C destination folder
        exif_c_dest = os.path.join(image_dir, "exif_C")
        os.makedirs(exif_c_dest, exist_ok=True)
        print(f"📁 Created exif_C folder: {exif_c_dest}")
        
        # Determine RTB output directory
        rtb_dir = os.path.join(ppk_folder if ppk_folder else image_dir, "RTB")
        
        if not os.path.exists(rtb_dir):
            print("No RTB output directory found - no images to copy to exif_C")
            return
        
        # Count total JPG files to copy
        total_jpg_files = 0
        for root, _, files in os.walk(rtb_dir):
            for file in files:
                if file.lower().endswith('.jpg'):
                    total_jpg_files += 1
        
        if total_jpg_files == 0:
            print("No JPG files found in RTB output directory")
            return
        
        print(f"📋 Found {total_jpg_files} JPG files to copy to exif_C folder")
        
        # Copy files with progress tracking
        files_copied = 0
        try:
            for root, _, files in os.walk(rtb_dir):
                for file in files:
                    if self.should_stop:
                        break
                        
                    if file.lower().endswith('.jpg'):
                        src_path = os.path.join(root, file)
                        dest_path = os.path.join(exif_c_dest, file)
                        
                        # Handle duplicate filenames
                        counter = 1
                        base_name, ext = os.path.splitext(file)
                        while os.path.exists(dest_path):
                            dest_path = os.path.join(exif_c_dest, f"{base_name}_{counter}{ext}")
                            counter += 1
                        
                        try:
                            shutil.copy2(src_path, dest_path)
                            files_copied += 1
                            
                            # Emit progress update
                            self.file_copy_progress.emit(files_copied, total_jpg_files, f"exif_C ({files_copied}/{total_jpg_files})")
                            
                        except Exception as e:
                            print(f"Failed to copy {src_path} to exif_C: {e}")
                            
                if self.should_stop:
                    break
            
            
            print(f"✅ Successfully copied {files_copied} JPG files to exif_C folder: {exif_c_dest}")
            
        except Exception as e:
            print(f"Error copying files to exif_C: {e}")

    def run_rtb(self, image_dir, base_dir, ppk_folder):
        try:
            print(f"Starting RTB processing with image_dir={image_dir}, base_dir={base_dir}, ppk_folder={ppk_folder}")
            bfile = self.find_latest_with_year_suffix(base_dir, {".o", ".obs"})
            nfile = self.find_latest_with_year_suffix(base_dir, {".n", ".nav"})
            gfile = self.find_latest_with_year_suffix(base_dir, {".g"})
            ant_fname = self.extract_ant_filename(bfile)
            ant_atx = None
            
            print(f"Found files: bfile={bfile}, nfile={nfile}, gfile={gfile}")
            if ant_fname:
                try:
                    os.makedirs(ATX_REPO_DIR, exist_ok=True)
                    candidate = os.path.join(ATX_REPO_DIR, ant_fname)
                    if os.path.isfile(candidate):
                        ant_atx = candidate
                        print(f"Using existing ATX file: {ant_atx}")
                    else:
                        url = f"https://geodesy.noaa.gov/ANTCAL/LoadFile?file={ant_fname}"
                        print(f"Downloading ATX from {url}")
                        urllib.request.urlretrieve(url, candidate)
                        if os.path.isfile(candidate):
                            ant_atx = candidate
                            print(f"Downloaded ATX to {ant_atx}")
                        else:
                            print("ATX download failed or file missing after download.")
                except Exception as e:
                    print(f"Failed to resolve ATX file for {ant_fname}: {e}")
            else:
                print("Antenna file name could not be derived from RINEX obs.")
            if not all((bfile, nfile)):
                print("ERROR: Could not find required .o or .n/.nav files in base directory.")
                print(f"bfile: {bfile}, nfile: {nfile}, gfile: {gfile}")
                return
            
            entries = [entry for entry in os.scandir(image_dir) if entry.is_dir()]
            print(f"Found {len(entries)} directories in image_dir={image_dir}")
            
            step = 0
            for entry in entries:
                subdir_path = entry.path
                print(f"Processing directory: {entry.name}")
                
                if ppk_folder:
                    output_dir = os.path.join(ppk_folder, "RTB")
                    os.makedirs(output_dir, exist_ok=True)
                    Rfile = self.find_latest(os.path.join(subdir_path, "*.RTK"))
                else:
                    Rfile = self.find_latest(os.path.join(subdir_path, "*.obs"))
                    output_dir = os.path.join(image_dir, "RTB")
                    os.makedirs(output_dir, exist_ok=True)
                
                Lfile = self.find_latest(os.path.join(subdir_path, "*Timestamp.MRK"))
                print(f"Files for processing: Lfile={Lfile}, Rfile={Rfile}, output_dir={output_dir}")
                
                missing = []
                required = [Lfile, Rfile, bfile, nfile, subdir_path, output_dir]
                optional = [gfile]
                for path in required:
                    if not path or not os.path.exists(path):
                        missing.append(path)
                # Only enforce gfile if it exists
                if gfile and not os.path.exists(gfile):
                    missing.append(gfile)
                
                if missing:
                    print(f"Skipping {entry.name}: missing files/folders: {missing}")
                    step += 1
                    self.file_copy_progress.emit(step, len(entries), f"Skipped {entry.name}")
                    continue
                
                cli = r"C:\\Program Files\\REDToolBox\\resources\\assets\\REDToolBoxCLI\\REDToolBoxCLI.exe"
                device_arg = "dji_l2" if self.sensor_choice == "L2" else "dji"
                cmd = [
                    cli, "mapping",
                    "--device", device_arg,
                    "--correction-type", "ppk",
                    "--output-format", "exif",
                    "--output-format", "pdf",
                    "--output-format", "textfile",
                    "--image-dir", subdir_path,
                    "--image-ending", ".jpg",
                    "--log-file", Lfile,
                    "--rover-file", Rfile,
                    "--base-file", bfile,
                    "--nav-file", nfile,
                    "--output-dir", output_dir,
                    "--log-level", "verbose",
                    "--create-timestamped-output-folder",
                    "--atx-file", ant_atx
                ]
                if gfile:
                    cmd.extend(["--gnav-file", gfile])
                
                print(f"Executing command: {' '.join(cmd)}")
                try:
                    self.status_update.emit(f"Processing folder: {entry.name} ({step + 1}/{len(entries)})")
                    subprocess.run(cmd, check=True)
                    step += 1
                    self.file_copy_progress.emit(step, len(entries), f"RTB processed: {entry.name}")
                except subprocess.CalledProcessError as e:
                    print(f"Error occurred while processing {entry.name}: {e}")
                    print(f"Command: {cmd}")
                    step += 1
                    self.file_copy_progress.emit(step, len(entries), f"RTB error: {entry.name}")
        
            self.status_update.emit("RTB processing completed.")
            
            # Copy RTB images to exif_C folder first
            self.copy_rtb_images_to_exif_c(image_dir, ppk_folder, entries)
            
            # Find and open PDFs generated by RTB processing AFTER copying is done
            pdfs_to_open = []
            if ppk_folder:
                output_dir = os.path.join(ppk_folder, "RTB")
            else:
                output_dir = os.path.join(image_dir, "RTB")
                
            for root, _, files in os.walk(output_dir):
                for file in files:
                    if file.lower().endswith('.pdf'):
                        pdfs_to_open.append(os.path.join(root, file))

            for pdf_path in pdfs_to_open:
                print(f"Opening PDF: {pdf_path}")
                try:
                    os.startfile(pdf_path)
                except Exception as e:
                    print(f"Could not open PDF {pdf_path}: {e}")
            
        except Exception as e:
            self.logger.exception("Critical error in run_rtb")
            self.error_occurred.emit(f"Critical error in run_rtb: {e}")
    
    def find_latest(self, glob_pattern):
        """Find the latest file matching a glob pattern"""
        try:
            files = sorted(glob.glob(glob_pattern), key=os.path.getmtime)
            return files[-1] if files else None
        except Exception as e:
            print(f"Error finding latest file with pattern {glob_pattern}: {e}")
    
    def find_latest_with_year_suffix(self, folder_path, target_exts):
        """
        Find the newest file whose extension matches target_exts, allowing two-digit year prefixes
        (e.g., .25o, .24n, .25mix). Returns the full file path.
        """
        latest_path = None
        latest_mtime = -1
        try:
            for entry in os.scandir(folder_path):
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()  # includes dot
                norm_ext = ext
                if len(norm_ext) > 3 and norm_ext[1:3].isdigit():
                    norm_ext = "." + norm_ext[3:]
                if norm_ext in target_exts:
                    mtime = entry.stat().st_mtime
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        latest_path = entry.path
        except Exception as e:
            print(f"Error scanning {folder_path} for extensions {target_exts}: {e}")
        return latest_path

    def extract_ant_filename(self, rinex_obs_path):
        """
        Read a RINEX obs file and extract antenna descriptor from the 'ANT # / TYPE' line.
        Returns a string like 'TRMR10_NONE.atx' or None if not found.
        """
        if not rinex_obs_path or not os.path.isfile(rinex_obs_path):
            return None
        try:
            with open(rinex_obs_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "ANT # / TYPE" in line:
                        parts = [p for p in line.split() if p]
                        if len(parts) >= 2:
                            return f"{parts[0]}_{parts[1]}.atx"
                        break
        except Exception as e:
            print(f"Failed to extract antenna info from {rinex_obs_path}: {e}")
        return None
    
    def L2_rename(self, file_path, root_dir):
        """Rename RINEX files for L2 processing"""
        file_to_copy_name = os.path.basename(file_path)
        for subdir, dirs, files in os.walk(root_dir):
            rtk_file = None
            for filename in files:
                if filename.lower().endswith(".rtk"):
                    rtk_file = filename
                    break
            if rtk_file:
                rtk_filename_without_ext = os.path.splitext(rtk_file)[0]
                destination_path = os.path.join(subdir, file_to_copy_name)
                if not os.path.exists(destination_path):
                    print(f"Copying '{file_to_copy_name}' to '{subdir}'")
                    shutil.copy(file_path, destination_path)
                else:
                    print(f"File '{file_to_copy_name}' already exists in '{subdir}'.")
                new_file_name = rtk_filename_without_ext + ".obs"
                new_file_path = os.path.join(subdir, new_file_name)
                print(f"Renaming '{file_to_copy_name}' to '{new_file_name}' in folder '{subdir}'")
                shutil.move(destination_path, new_file_path)
            else:
                print(f"No .rtk file found in folder '{subdir}'. Skipping folder.")
        print("L2_rename script completed!")
    
    def open_generated_pdfs(self, image_dir, ppk_folder):
        """Open all PDFs generated by RTB processing"""
        # Determine the output directory based on sensor type
        if ppk_folder:
            output_dir = os.path.join(ppk_folder, "RTB")
        else:
            output_dir = os.path.join(image_dir, "RTB")
        
        if not os.path.exists(output_dir):
            print("No RTB output directory found - no PDFs to open")
            return
        
        pdfs_opened = 0
        try:
            # Recursively search for all PDF files in the RTB output directory
            for root, _, files in os.walk(output_dir):
                for file in files:
                    if file.lower().endswith('.pdf'):
                        pdf_path = os.path.join(root, file)
                        print(f"🚀 Opening PDF: {pdf_path}")
                        try:
                            os.startfile(pdf_path)
                            pdfs_opened += 1
                        except Exception as e:
                            print(f"⚠️ Could not open PDF {pdf_path}: {e}")
            
            if pdfs_opened > 0:
                print(f"✅ Opened {pdfs_opened} PDF report(s)")
            else:
                print("No PDF files found in RTB output directory")
                
        except Exception as e:
            print(f"Error opening PDFs: {e}")
    
    

class DataIntakeUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.log_file_path = None
        
        # Define standardized style variables
        self.STYLE_LABEL_PRIMARY = "color: #113e59; background: #eaf6fa; border: 1px solid #ffd457; border-radius: 6px; padding: 5px; margin: 5px;"
        self.STYLE_LABEL_SECONDARY = "color: #113e59; background: #fffbe6; border: 1px solid #ffd457; border-radius: 6px; padding: 5px; margin: 5px;"
        self.STYLE_LABEL_WARNING = "color: #D32F2F; background: #ffd457; border-radius: 6px; padding: 10px; margin: 10px;"
        self.STYLE_LABEL_SUCCESS = "color: #228B22; background: #eaf6fa; border-radius: 6px; padding: 5px; margin: 5px;"
        self.STYLE_LABEL_LIST = "background: #f8f8f8; border: 1px solid #ffd457; color: #113e59; border-radius: 6px; padding: 5px; margin: 5px;"
        self.STYLE_LABEL_DROP = "background: #fffbe6; border: 2px dashed #ffd457; color: #113e59; border-radius: 6px; padding: 5px; margin: 5px;"
        self.STYLE_LABEL_DROP_RIDGE = "background: #fffbe6; border: 2px ridge #ffd457; border-radius: 6px; color: #113e59; padding: 5px; margin: 5px;"
        self.STYLE_LABEL_TRANSPARENT = "background: transparent; color: #eaf6fa; padding: 5px; margin: 5px;"
        self.STYLE_LABEL_PROGRESS = "color: #113e59; background: #eaf6fa; border: 1px solid #ffd457; border-radius: 6px; padding: 8px; margin: 5px;"
        
        self.STYLE_BUTTON_PRIMARY = "color: #113e59; background: #ffd457; border-radius: 6px; border: 1px solid #eaf6fa; padding: 5px; margin: 5px;"
        self.STYLE_BUTTON_SECONDARY = "color: #113e59; background: #eaf6fa; border-radius: 6px; border: 1px solid #ffd457; padding: 5px; margin: 5px;"
        self.STYLE_BUTTON_DANGER = "color: #fff; background: #D32F2F; border-radius: 6px; padding: 2px 8px; margin: 5px;"
        self.STYLE_BUTTON_MAIN = """
            QPushButton {
                background: #ffd457;
                color: #113e59;
                border-radius: 6px;
                font-weight: bold;
                border: 1px solid #eaf6fa;
                padding: 5px;
                margin: 5px;
            }
            QPushButton:pressed {
                background: #228B22;
                color: #fff;
            }
        """
        
        self.STYLE_INPUT = "background: #eaf6fa; border: 1px solid #ffd457; border-radius: 6px; color: #113e59; padding: 5px; margin: 5px;"
        
        self.STYLE_PROGRESS_BAR = """
            QProgressBar {
                color: #113e59; 
                background: #eaf6fa; 
                border: 1px solid #ffd457; 
                border-radius: 6px; 
                padding: 2px; 
                margin: 5px;
                text-align: center;
                font-size: 14px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: #ffd457;
                border-radius: 4px;
            }
        """
        
        self.STYLE_MESSAGEBOX = """
            QMessageBox {
                background: #113e59;
                border: 1px solid #ffd457;
                border-radius: 6px;
            }
            QMessageBox QLabel {
                color: #ffd457;
                font-family: 'Segoe UI';
                font-size: 12pt;
            }
            QPushButton {
                background: #ffd457;
                color: #113e59;
                border-radius: 6px;
                padding: 5px;
                margin: 5px;
            }
            QPushButton:pressed {
                background: #228B22;
                color: #fff;
            }
        """
        
        self.selected_folder = ""
        self.data_source_folders = []
        self.base_data_paths = []
        self.base_data_is_rinex = False
        self.processing_worker = None
        self.current_progress_bar = None
        self.current_progress_label = None
        
        self.setWindowTitle("Data Intake")
        self.setGeometry(100, 100, 1200, 1000)
        self.setStyleSheet("background-color: #113e59; color: #113e59;")
        
        # Set window icon to logo.png if available
        if os.path.exists(r"Z:\Survey\UT\_GabeA\PanoSandbox\logo-small.png"):
            self.setWindowIcon(QIcon(r"Z:\Survey\UT\_GabeA\PanoSandbox\logo-small.png"))
        
        self.show_placeholder_popup()
        self.init_ui()
        self.load_last_folder()

    def show_placeholder_popup(self):
        from PyQt5.QtWidgets import QDialog
        popup = QDialog(self)
        popup.setWindowTitle("Data Intake Warning") 
        popup.setGeometry(400, 250, 400, 180)
        layout = QVBoxLayout(popup)
        label = QLabel("Before using this app, ensure you have the following files and folders:\n\nGCP file\n\nBase file\n\nDrone data")
        label.setFont(QFont("Segoe UI Bold", 14, QFont.Bold))
        label.setStyleSheet(self.STYLE_LABEL_WARNING)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        close_button = QPushButton("OK")
        close_button.setFont(QFont("Segoe UI", 12, QFont.Bold))
        close_button.setStyleSheet(self.STYLE_BUTTON_SECONDARY)
        close_button.clicked.connect(popup.accept)
        layout.addWidget(close_button)
        popup.exec_()

    def init_ui(self):
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        main_layout = QVBoxLayout(self.central_widget)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll.setWidget(scroll_content)
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setAlignment(Qt.AlignLeft)
        main_layout.addWidget(scroll)

        # --- Logo ---
        from PyQt5.QtGui import QPixmap
        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        try:
            pixmap = QPixmap(r"Z:\Survey\UT\_GabeA\PanoSandbox\logo.png")
            if not pixmap.isNull():
            # Increased logo size to 260x260
                self.logo_label.setPixmap(pixmap.scaled(550, 550, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            pass
        scroll_layout.addWidget(self.logo_label, alignment=Qt.AlignHCenter | Qt.AlignTop)

        # --- Folder selection ---
        folder_layout = QHBoxLayout()
        folder_layout.setAlignment(Qt.AlignHCenter)
        self.label = QLabel("Selected folder:")
        self.label.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.label.setStyleSheet(self.STYLE_LABEL_PRIMARY)
        self.label.setMinimumWidth(180)
        folder_layout.addWidget(self.label)
        
        self.folder_path_display = QLabel("Path to 3D data folder")
        self.folder_path_display.setFont(QFont("Segoe UI", 12))
        self.folder_path_display.setStyleSheet(self.STYLE_LABEL_SECONDARY)
        self.folder_path_display.setMinimumWidth(420)
        folder_layout.addWidget(self.folder_path_display)
        
        self.folder_clear_btn = QPushButton("Clear")
        self.folder_clear_btn.setFont(QFont("Segoe UI", 10))
        self.folder_clear_btn.setStyleSheet(self.STYLE_BUTTON_DANGER)
        self.folder_clear_btn.clicked.connect(lambda: self.folder_path_display.setText(""))
        folder_layout.addWidget(self.folder_clear_btn)
        
        self.btn_choose_dir = QPushButton("Choose Folder")
        self.btn_choose_dir.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.btn_choose_dir.setStyleSheet(self.STYLE_BUTTON_PRIMARY)
        self.btn_choose_dir.setMinimumWidth(130)
        self.btn_choose_dir.clicked.connect(self.choose_folder)
        folder_layout.addWidget(self.btn_choose_dir)
        scroll_layout.addLayout(folder_layout)

        # --- Base file drag-and-drop ---
        base_file_layout = QHBoxLayout()
        base_file_layout.setAlignment(Qt.AlignHCenter)
        self.base_file_drop = QLabel("Drop Base Data file(s) here or click to select")
        self.base_file_drop.setFont(QFont("Segoe UI", 12))
        self.base_file_drop.setStyleSheet(self.STYLE_LABEL_DROP)
        self.base_file_drop.setFixedHeight(55)
        self.base_file_drop.setFixedWidth(500)
        self.base_file_drop.setAcceptDrops(True)
        self.base_file_drop.setWordWrap(True)
        self.base_file_drop.mousePressEvent = self.choose_base_file_event
        base_file_layout.addWidget(self.base_file_drop)
        
        self.label5 = QLabel("Base data file not selected")
        self.label5.setFont(QFont("Segoe UI", 12))
        self.label5.setStyleSheet("color: #113e59; background: #eaf6fa; border: none; border-radius: 6px; padding: 5px; margin: 5px;")
        self.label5.setFixedHeight(55)
        self.label5.setFixedWidth(450)
        base_file_layout.addWidget(self.label5)
        
        self.basefile_clear_btn = QPushButton("Clear")
        self.basefile_clear_btn.setFont(QFont("Segoe UI", 10))
        self.basefile_clear_btn.setStyleSheet(self.STYLE_BUTTON_DANGER)
        self.basefile_clear_btn.clicked.connect(self.clear_base_file)
        base_file_layout.addWidget(self.basefile_clear_btn)
        scroll_layout.addLayout(base_file_layout)

        # --- Data source drag area ---
        self.drop_label = QLabel("Drop Source Data Folders Here")
        self.drop_label.setFont(QFont("Segoe UI", 12))
        self.drop_label.setStyleSheet(self.STYLE_LABEL_DROP_RIDGE)
        self.drop_label.setFixedHeight(64)
        self.drop_label.setAcceptDrops(True)
        scroll_layout.addWidget(self.drop_label)
        self.drop_label.installEventFilter(self)
        self.base_file_drop.installEventFilter(self)

        # --- Data source folders list ---
        self.data_source_list_label = QLabel("No data source folders selected.")
        self.data_source_list_label.setFont(QFont("Segoe UI", 11))
        self.data_source_list_label.setStyleSheet(self.STYLE_LABEL_LIST)
        self.data_source_list_label.setWordWrap(True)
        self.data_source_list_label.hide()
        scroll_layout.addWidget(self.data_source_list_label)
        
        self.data_source_list_clear_btn = QPushButton("Clear drone data sources")
        self.data_source_list_clear_btn.setFont(QFont("Segoe UI", 10))
        self.data_source_list_clear_btn.setStyleSheet(self.STYLE_BUTTON_DANGER)
        self.data_source_list_clear_btn.clicked.connect(self.clear_data_sources)
        scroll_layout.addWidget(self.data_source_list_clear_btn)

        # --- Client/Project/Sensor inputs ---
        input_row = QHBoxLayout()
        input_row.setAlignment(Qt.AlignHCenter)

        self.label3 = QLabel("Client:")
        self.label3.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.label3.hide()
        self.label3.setStyleSheet(self.STYLE_LABEL_TRANSPARENT)
        input_row.addWidget(self.label3)
        
        self.client_ent = QLineEdit()
        self.client_ent.setFont(QFont("Segoe UI", 12))
        self.client_ent.setStyleSheet(self.STYLE_INPUT)
        self.client_ent.setFixedWidth(180)
        self.client_ent.hide()
        self.client_ent.textChanged.connect(self.on_sensor_choice)
        input_row.addWidget(self.client_ent)

        self.label4 = QLabel("Project:")
        self.label4.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.label4.hide()
        self.label4.setStyleSheet(self.STYLE_LABEL_TRANSPARENT)
        input_row.addWidget(self.label4)
        
        self.project_ent = QLineEdit()
        self.project_ent.setFont(QFont("Segoe UI", 12))
        self.project_ent.setStyleSheet(self.STYLE_INPUT)
        self.project_ent.setFixedWidth(180)
        self.project_ent.hide()
        self.project_ent.textChanged.connect(self.on_sensor_choice)
        input_row.addWidget(self.project_ent)

        self.lbl = QLabel("Sensor type:")
        self.lbl.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.lbl.hide()
        self.lbl.setStyleSheet(self.STYLE_LABEL_TRANSPARENT)
        input_row.addWidget(self.lbl)
        
        self.dropdown = QComboBox()
        self.dropdown.setFont(QFont("Segoe UI", 12))
        self.dropdown.setStyleSheet(self.STYLE_INPUT)
        self.dropdown.addItem("Select Sensor Type")  # Add placeholder item
        self.dropdown.addItems(sensor_list)
        self.dropdown.setCurrentIndex(0)  # Set the placeholder as the default
        self.dropdown.currentIndexChanged.connect(self.on_sensor_choice)
        self.dropdown.hide()
        input_row.addWidget(self.dropdown)
        scroll_layout.addLayout(input_row)
        
        self.create_lbl = QLabel("✅ All fields selected — ready to create folders.")
        self.create_lbl.setFont(QFont("Segoe UI", 12, QFont.Bold))
        self.create_lbl.setStyleSheet(self.STYLE_LABEL_SUCCESS)
        self.create_lbl.hide()
        self.create_lbl.setFixedHeight(60)
        scroll_layout.addWidget(self.create_lbl)
        
        self.create_btn = QPushButton("Start data intake processes")
        self.create_btn.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self.create_btn.setStyleSheet(self.STYLE_BUTTON_MAIN)
        self.create_btn.clicked.connect(self.start_create_subfolders_thread)
        self.create_btn.hide()
        scroll_layout.addWidget(self.create_btn)

        # Set alignment for all buttons in the layout
        scroll_layout.setAlignment(self.data_source_list_clear_btn, Qt.AlignHCenter)
        scroll_layout.setAlignment(self.create_btn, Qt.AlignHCenter)
        folder_layout.setAlignment(self.folder_clear_btn, Qt.AlignHCenter)
        folder_layout.setAlignment(self.btn_choose_dir, Qt.AlignHCenter)
        base_file_layout.setAlignment(self.basefile_clear_btn, Qt.AlignHCenter)

    def eventFilter(self, source, event):
        if source == self.drop_label:
            if event.type() == event.DragEnter:
                if event.mimeData().hasUrls():
                    event.accept()
                    return True
            elif event.type() == event.Drop:
                self.on_drop(event)
                return True
        elif source == self.base_file_drop:
            if event.type() == event.DragEnter:
                if event.mimeData().hasUrls():
                    event.accept()
                    return True
            elif event.type() == event.Drop:
                self.on_base_file_drop(event)
                return True
        return super().eventFilter(source, event)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select 3dData Folder", "E:/Data")
        if not folder:
            QMessageBox.warning(self, "Folder Required", "Please select a folder to continue.")
            return
        self.selected_folder = folder
        self.folder_path_display.setText(folder)
        self.save_last_folder(folder)

    def is_rinex_path(self, path):
        rinex_exts = ("o", "n", "g", "p", "l", "s", "obs", "rnx", "crx", "mix", "nav")
        return path.lower().endswith(rinex_exts)

    def choose_base_file_event(self, event):
        file_filter = "Base Data Files (*.T02 *.T04 *.t02 *.t04 *.??o *.??n *.??g *.??p *.??l *.??s *.o *.n *.g *.p *.l *.s *.rnx *.obs *.crx *.mix *.nav);;All Files (*)"
        files, _ = QFileDialog.getOpenFileNames(self, "Select Base Data file(s)", "", file_filter)
        if not files:
            return
        self.add_base_files(files)

    def on_base_file_drop(self, event):
        files = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path) and (path.lower().endswith(('.t02', '.t04')) or self.is_rinex_path(path)):
                files.append(path)
        if files:
            self.add_base_files(files)

    def add_base_files(self, files):
        """Add one or more base data files."""
        added_any = False
        for file in files:
            if not os.path.isfile(file):
                continue
            if not (file.lower().endswith(('.t02', '.t04')) or self.is_rinex_path(file)):
                continue
            if file not in self.base_data_paths:
                self.base_data_paths.append(file)
                added_any = True

        if added_any:
            self.base_data_is_rinex = all(self.is_rinex_path(f) for f in self.base_data_paths)
            display_names = [os.path.basename(f) for f in self.base_data_paths]
            self.label5.setText(f"Selected files ({len(display_names)}): {', '.join(display_names)}")
            self.set_names()
        elif not self.base_data_paths:
            self.label5.setText("Base data file not selected")

    def clear_base_file(self):
        self.base_data_paths = []
        self.base_data_is_rinex = False
        self.label5.setText("Base data file not selected")

    def set_names(self):
        self.label3.show()
        self.client_ent.show()
        self.label4.show()
        self.project_ent.show()
        self.lbl.show()
        self.dropdown.show()

    def on_sensor_choice(self):
        client_name = self.client_ent.text()
        project_name = self.project_ent.text()
        sensor_choice = self.dropdown.currentText()
        # Only show if all fields are filled AND at least one data source folder is present
        if client_name and project_name and sensor_choice != "Select Sensor Type" and self.data_source_folders:
            self.create_lbl.show()
            self.create_btn.show()
        else:
            self.create_lbl.hide()
            self.create_btn.hide()

    def save_last_folder(self, folder_path):
        """Persist the last selected folder to a small marker file."""
        if not folder_path:
            return
        try:
            os.makedirs(os.path.dirname(LAST_FOLDER_FILE), exist_ok=True)
            with open(LAST_FOLDER_FILE, "w", encoding="utf-8") as f:
                f.write(folder_path)
        except Exception as e:
            print(f"Could not save last folder: {e}")

    def load_last_folder(self):
        """Load the last selected folder if it exists and is valid."""
        try:
            if os.path.isfile(LAST_FOLDER_FILE):
                with open(LAST_FOLDER_FILE, "r", encoding="utf-8") as f:
                    folder = f.read().strip()
                if folder and os.path.isdir(folder):
                    self.selected_folder = folder
                    self.folder_path_display.setText(folder)
        except Exception as e:
            print(f"Could not load last folder: {e}")

    def on_drop(self, event):
        try:
            # Append dropped folders, skipping duplicates
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if os.path.isdir(path):
                    # Skip if already added
                    if path in self.data_source_folders:
                        continue
                    try:
                        folder_size_bytes = self.get_folder_size(path)
                        folder_size_mb = folder_size_bytes / (1024 * 1024)
                        if folder_size_mb < 100:
                            continue
                        # Add new valid folder
                        self.data_source_folders.append(path)
                    except Exception as e:
                        print(f"Error processing folder {path}: {e}")
                        continue
                        
            if self.data_source_folders:
                self.drop_label.setText(f"{len(self.data_source_folders)} folder(s) selected:")
                self.data_source_list_label.setText("\n".join(self.data_source_folders))
                self.data_source_list_label.show()
                self.on_sensor_choice()  # Check if ready to show create button
            else:
                self.data_source_list_label.setText("No data source folders selected.")
                self.data_source_list_label.show()
                QMessageBox.warning(self, "No Folders", "No valid data source folders dropped.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"An error occurred while processing dropped folders:\n{str(e)}")
            self.data_source_folders = []

    def get_folder_size(self, folder_path):
        total_size = 0
        try:
            for dirpath, _, filenames in os.walk(folder_path):
                for file in filenames:
                    file_path = os.path.join(dirpath, file)
                    try:
                        total_size += os.path.getsize(file_path)
                    except (OSError, FileNotFoundError):
                        continue
        except Exception as e:
            print(f"Error calculating folder size for {folder_path}: {e}")
        return total_size

    def clear_data_sources(self):
        """Clear all data source folders and update the UI"""
        self.data_source_folders = []
        self.data_source_list_label.setText("No data source folders selected.")
        self.data_source_list_label.hide()
        self.drop_label.setText("Drop Source Data Folders Here")
        self.on_sensor_choice()  # Re-evaluate if create button should be shown

    def start_create_subfolders_thread(self):
        # Validate inputs before starting processing
        if not self.selected_folder:
            QMessageBox.warning(self, "No Folder", "Please choose a folder first.")
            return
        if not self.dropdown.currentText():
            QMessageBox.warning(self, "No sensor selected", "Please pick a sensor type from dropdown")
            return
        if not self.data_source_folders:
            QMessageBox.warning(self, "No Data Sources", "Please select data source folders.")
            return
        if not self.base_data_paths:
            QMessageBox.warning(self, "No Base File", "Please select at least one base data file.")
            return
            
        CLIENT = self.client_ent.text().strip()
        PROJECT = self.project_ent.text().strip()
        choice = self.dropdown.currentText()
        
        if not CLIENT or not PROJECT:
            QMessageBox.warning(self, "Missing Information", "Please enter both client and project names.")
            return

        # Disable the start button to prevent multiple simultaneous operations
        self.create_btn.setEnabled(False)
        self.create_btn.setText("Processing...")
        
        # Create and start the worker thread
        self.processing_worker = ProcessingWorker(
            self.selected_folder,
            self.data_source_folders,
            self.base_data_paths,
            CLIENT,
            PROJECT,
            choice,
            self.base_data_is_rinex
        )
        
        # Connect worker signals to UI slots
        self.processing_worker.file_copy_progress.connect(self.update_file_copy_progress)
        self.processing_worker.status_update.connect(self.update_status)
        self.processing_worker.error_occurred.connect(self.handle_processing_error)
        self.processing_worker.processing_complete.connect(self.handle_processing_complete)
        self.processing_worker.finished.connect(self.cleanup_after_processing)
        
        # Start the worker thread
        self.processing_worker.start()
    
    def update_file_copy_progress(self, current, total, folder_name):
        """Update the file copying progress bar"""
        if not self.current_progress_bar:
            # Determine if this is RTB processing or file copying
            if "RTB" in folder_name or "processed" in folder_name:
                self.setup_progress_bar("Processing RTB Folders...", total)
            else:
                self.setup_progress_bar("Copying Files...", total)

        if self.current_progress_bar and self.current_progress_label:
            # Update progress bar maximum if it's changed (for RTB processing)
            if total != self.current_progress_bar.maximum():
                self.current_progress_bar.setMaximum(total)

            self.current_progress_bar.setValue(current)
            
            # Check if this is RTB processing
            if "RTB" in folder_name:
                if "processed" in folder_name:
                    # Extract folder name from "RTB processed: DJI_20231201_101500_002"
                    try:
                        folder_part = folder_name.split("RTB processed:")[-1].strip()
                        self.current_progress_label.setText(f"RTB Processing: {folder_part} ({current}/{total})")
                    except:
                        self.current_progress_label.setText(f"RTB Processing: {current}/{total} - {folder_name}")
                elif "error" in folder_name:
                    # Extract folder name from "RTB error: DJI_20231201_101500_002"
                    try:
                        folder_part = folder_name.split("RTB error:")[-1].strip()
                        self.current_progress_label.setText(f"RTB Error: {folder_part} ({current}/{total})")
                    except:
                        self.current_progress_label.setText(f"RTB Error: {current}/{total} - {folder_name}")
                else:
                    self.current_progress_label.setText(f"RTB Processing: {current}/{total} - {folder_name}")
            elif "exif_C" in folder_name:
                # This is copying JPGs to exif_C folder
                self.current_progress_label.setText(f"Copying to exif_C: {folder_name}")
            else:
                # This is regular file copying
                self.current_progress_label.setText(f"Copying files: {current}/{total} - {folder_name}")
            
            QApplication.processEvents()
    def update_status(self, message):
        """Update the status label"""
        try:
            logger.info(message)
        except Exception:
            pass
        if self.current_progress_label:
            self.current_progress_label.setText(message)
            QApplication.processEvents()
    
    def setup_progress_bar(self, label_text, maximum_value):
        """Create and show progress bar"""
        try:
            self.current_progress_bar = QProgressBar()
            self.current_progress_bar.setMinimum(0)
            self.current_progress_bar.setMaximum(maximum_value)
            self.current_progress_bar.setTextVisible(True)
            self.current_progress_bar.setAlignment(Qt.AlignCenter)
            self.current_progress_bar.setFixedHeight(35)
            self.current_progress_bar.setStyleSheet(self.STYLE_PROGRESS_BAR)
            
            self.current_progress_label = QLabel(label_text)
            self.current_progress_label.setFont(QFont("Segoe UI", 14, QFont.Bold))
            self.current_progress_label.setStyleSheet(self.STYLE_LABEL_PROGRESS)
            
            main_layout = self.central_widget.layout()
            main_layout.addWidget(self.current_progress_label)
            main_layout.addWidget(self.current_progress_bar)
        except Exception as e:
            print(f"Error setting up progress bar: {e}")
    
    def cleanup_progress_bar(self):
        """Remove and cleanup progress bar"""
        try:
            if self.current_progress_bar and self.current_progress_label:
                main_layout = self.central_widget.layout()
                self.current_progress_label.hide()
                self.current_progress_bar.hide()
                main_layout.removeWidget(self.current_progress_label)
                main_layout.removeWidget(self.current_progress_bar)
                self.current_progress_label.deleteLater()
                self.current_progress_bar.deleteLater()
                self.current_progress_label = None
                self.current_progress_bar = None
        except Exception as e:
            print(f"Error cleaning up progress bar: {e}")
    
    def handle_processing_error(self, error_message):
        """Handle errors from the processing thread"""
        logger.error(f"Processing error: {error_message}")
        self.cleanup_progress_bar()
        error_box = QMessageBox(self)
        error_box.setIcon(QMessageBox.Critical)
        error_box.setWindowTitle("Processing Error")
        error_box.setText(f"An error occurred during processing:\n\n{error_message}")
        # Black and white styling
        error_box.setStyleSheet("""
            QMessageBox {
                background: white;
                border: 2px solid black;
                border-radius: 6px;
            }
            QMessageBox QLabel {
                color: black;
                font-family: 'Segoe UI';
                font-size: 12pt;
            }
            QPushButton {
                background: white;
                color: black;
                border: 1px solid black;
                border-radius: 6px;
                padding: 5px;
                margin: 5px;
            }
            QPushButton:pressed {
                background: black;
                color: white;
            }
        """)
        error_box.exec_()
        self.reset_ui_after_processing()
    
    def handle_processing_complete(self, CLIENT, PROJECT, DATE_CURR, sensor_folder_path, first_image_found, files_copied, date_folder_path):
        """Handle successful completion of processing"""
        logger.info("Processing finished successfully")
        if self.log_file_path:
            logger.info(f"Log file saved to: {self.log_file_path}")
        self.cleanup_progress_bar()
        self.show_done_message(CLIENT, PROJECT, DATE_CURR, sensor_folder_path, first_image_found, files_copied, date_folder_path)
        self.reset_ui_after_processing()
    
    def cleanup_after_processing(self):
        """Clean up after worker thread finishes"""
        if self.processing_worker:
            self.processing_worker.deleteLater()
            self.processing_worker = None
    
    def reset_ui_after_processing(self):
        """Reset UI elements after processing completes or fails"""
        self.create_btn.setEnabled(True)
        self.create_btn.setText("Start data intake processes")

    def show_done_message(self, CLIENT, PROJECT, DATE_CURR, sensor_folder_path, first_image_found, files_copied, date_folder_path=None):
        if not date_folder_path:
            date_folder_path = os.path.join(self.selected_folder, CLIENT, PROJECT, DATE_CURR)
        
        msg_text = f"Created nested folders and sensor subfolders under:\n{sensor_folder_path}"
        if first_image_found:
            msg_text += f"\n\nImage used for date:\n{first_image_found}"
        
        # Compute stats using passed values and selections
        total_images = files_copied
        num_folders = len(self.data_source_folders)
        project_path = os.path.join(self.selected_folder, CLIENT, PROJECT)
        msg_text += f"\n\nTotal images processed: {total_images}"
        msg_text += f"\nData source folders processed: {num_folders}"
        msg_text += f"\nProject folder path: {project_path}"
        
        # --- Recursively search for all PDF files and open them ---
        for root, _, files in os.walk(date_folder_path):
            for file in files:
                if file.lower().endswith('.pdf'):
                    pdf_path = os.path.join(root, file)
                    print(f"🚀 Opening PDF: {pdf_path}")
                    try:
                        os.startfile(pdf_path)
                    except Exception as e:
                        print(f"⚠️ Could not open PDF {pdf_path}: {e}")
        
        # Play sound notification (wav file)
        try:
            QSound.play(r"Z:\Survey\UT\_GabeA\PanoSandbox\super_mario.wav")
        except:
            pass  # Sound file might not exist
        
        # Show styled message box
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Information)
        msg_box.setWindowTitle("Process Completed")
        msg_box.setText(msg_text)
        msg_box.setStyleSheet(self.STYLE_MESSAGEBOX)
        # Show OK button and open project folder when clicked
        msg_box.setStandardButtons(QMessageBox.Ok)
        ret = msg_box.exec_()
        if ret == QMessageBox.Ok:
            # Open the main project directory
            os.startfile(project_path)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DataIntakeUI()
    window.show()
    sys.exit(app.exec_())
    window = DataIntakeUI()
    window.show()
    sys.exit(app.exec_())
    msg_box.setWindowTitle("Process Completed")
    msg_box.setText(msg_text)
    msg_box.setStyleSheet(self.STYLE_MESSAGEBOX)
    # Show OK button and open project folder when clicked
    msg_box.setStandardButtons(QMessageBox.Ok)
    ret = msg_box.exec_()
    if ret == QMessageBox.Ok:
        # Open the main project directory
        os.startfile(project_path)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DataIntakeUI()
    window.show()
    sys.exit(app.exec_())
    window = DataIntakeUI()
    window.show()
    sys.exit(app.exec_())
