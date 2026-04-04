Introduction

Welcome to the GeoStudio Python scripting API setup guide. This document will help you install the required libraries using the provided `.whl` file.

All official documentation and examples available for this API can be found at the following link:
https://aka.bentley.com/GeoStudio.url.ScriptingAPIDocs/1

Prerequisites

Before you begin scripting, ensure you have the following:

1. A Python environment with python>=3.12.1,<3.13.0.
2. The `.whl` file and `requirements.txt` file provided by us, which contain the necessary libraries. These files should be located in the same folder as this README.

Installing the Required Libraries

1.	From the Windows Search Menu: type 'Geostudio', one of the items under 'Apps' should be 'GeoStudio 2025.2.1 API Folder'. Click it to open an explorer window to the 'API' folder.
2.	Right click the file: 'requirements.txt' and select 'Copy as path'.
		Note: if you have an existing requirements file you would like to use, first merge the contents of the requirements file in the API folder into your existing one.
		Then copy the path to your existing requirements file and use that path in the command below.
3. 	Install the Requirements File: 
		Run the following command in your desired Python environment to install the libraries:
			pip install -r <hit control+v to paste the path here>
			example: pip install -r "C:\Program Files\Seequent\GeoStudio 2025.2\API\requirements.txt"
		Note: the gsi-2025.2.1-py3-none-any.whl file could have been installed directly; however, additional libraries may be added to the requirements.txt file in a future release.

Verification

To verify the installation, you can run a simple script to check if the libraries are correctly installed.
Ensure that GeoStudio is licensed before proceeding.

Create a Python file (e.g., `test_installation.py`) and add the following code:
			import gsi
			print("Installation successful!")

Run the script:
			python test_installation.py

If you see the message "Installation successful!", you are all set.