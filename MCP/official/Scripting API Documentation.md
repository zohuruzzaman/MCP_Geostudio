# GeoStudio Scripting API - Getting Started and Protocol Reference

This document provides comprehensive technical documentation for implementing automated geotechnical analysis workflows using the GeoStudio Scripting API. The guide covers fundamental concepts, implementation patterns, and practical examples for programmatic control of GeoStudio operations.

## What is the GeoStudio Scripting API?

The GeoStudio Scripting API provides programmatic access to the functionalities of GeoStudio using Python. The API enables automated manipulation of project files, analysis execution, results extraction, and much more. Key capabilities include:

- Opening and modifying project files
- Changing material properties and boundary conditions
- Running analyses programmatically
- Extracting and processing results
- Performing parametric studies with hundreds of variations

The API implements a remote procedure call framework for communication between Python scripts and the GeoStudio service.

---

## Reference Documentation
This guide is to be used in conjunction with the official API reference sheets, which contain: 
- All available request types (GetRequest, SetRequest, SolveAnalysesRequest, etc.)
- All response message formats
- The full list of enums (like ResultType, DataParamType, etc.)
- Fields and their expected data types

In summary:
- **This guide:** demonstrates the patterns and workflows (open, query, set, solve, extract).
- **The reference docs:** contain exact names, fields, and parameters that can be used in various request types when writing scripts.

For example, a search or the word 'water' in the generated documentation would help with the discovery of the data parameter gsi.DataParamType.eWaterPressureHead, which could be used in a query.

## Section 1: Environment Configuration

### Understanding the Communication Method

The Scripting API uses a remote procedure call framework to communicate between Python scripts and GeoStudio.

- GeoStudio service running as a background process
- Python client sending structured requests via gRPC protocol
- Bidirectional communication with structured response handling
- Network-style error handling requirements due to inter-process communication

### Environment Set-Up

Before accessing the scripting API, configure the Python environment according to the provided installation specifications. These can be found by navigating through the Start menu: All Applications → Seequent folder → API folder. This location contains both a ReadMe file and a requirements.txt file. The requirements.txt includes a wheel file that lists all the Python packages needed for the Scripting API to function properly. Follow the instructions in the ReadMe.txt to ensure all the necessary dependencies are installed correctly.

### Essential Imports

Every script needs these imports at the top:

```python
import os
import grpc
from google.protobuf.json_format import MessageToDict
import gsi
```

**What each import does:**
- `os`: For file path operations and setting the GSI path
- `grpc`: For handling communication errors
- `MessageToDict`: Converts GSI responses into readable Python dictionaries
- `gsi`: The main GeoStudio API module

---

## Section 2: Opening and Closing Projects

### The Project Lifecycle

All API workflows implement a consistent project lifecycle:
- Project file initialization (must be .gsz format)
- Data manipulation operations (query, modify, analyze)
- Resource cleanup and project closure

### Opening a Project

The most basic way to open a project is as follows:

```python
project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
```

**Important notes:**
- Raw strings (`r""`) are preferred for file paths as to avoid backslash issues
    - Without the r at the beginning, Python might misinterpret a backslash as an instruction to do something else (like creating a new line with \n), which would break the file path. The raw string tells Python to treat the characters literally.
- The project file must be a `.gsz` file

### Closing a Project

Projects must always be closed upon completion:

```python
project.Close()
```

**Why closing matters:**
- Frees up computer memory
- Prevents GeoStudio from getting overloaded with open projects
- Good programming practice

### The Safe Way (Recommended)

The problem with the simple approach is that if something goes wrong, the project might not get closed properly. Here's the safer approach:

```python
project = None
try:
    project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
    # Your work goes here
    print("Project opened successfully!")
    
except Exception as e:
    print(f"Something went wrong: {e}")
    
finally:
    # This runs no matter what happens above
    if project is not None:
        project.Close()
        print("Project closed safely")
```

**What this does:**
- `try:` - Attempt to open and work with the project
- `except:` - If anything goes wrong, catch the error and print it
- `finally:` - No matter what happens, close the project if it was opened

### First Complete Implementation Example

Here's a complete, working script that just opens and closes a project:

```python
import os
import gsi

def my_first_gsi_script():
    project = None
    try:
        # Open the project
        project_path = r"C:\Path\To\Your\Project.gsz"  # Change this to your file
        project = gsi.OpenProject(project_path)
        print("Project opened successfully!")
        
        # For now, we'll just print a message
        print("I'm working with the project...")
        
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        if project is not None:
            project.Close()
            print("Project closed")

# Run the script
if __name__ == '__main__':
    my_first_gsi_script()
```
---

## Section 3: Understanding Project Structure

### How GeoStudio Projects Are Organized
Understanding how GeoStudio organizes information is important before modify anything in a project. GeoStudio projects implement a hierarchical object model:

```
Project
├── Analysis 1 (e.g., "Steady-State Analysis")
│   ├── Materials
│   │   ├── "Upper Soil"
│   │   ├── "Lower Soil"
│   │   └── "Bedrock"
│   ├── Boundary Conditions
│   │   ├── "Head Boundary"
│   │   └── "Flux Boundary"
│   └── Geometry
│       ├── Regions (areas of different materials)
│       ├── Lines (boundaries, interfaces)
│       └── Points (specific locations)
└── Analysis 2 (e.g., "Transient Analysis")
    └── (similar structure)
```

### Object Paths: The Address System

Object access requires precise path specification using structured addressing:

**Examples of object paths:**
```python
# A material named "Upper Soil"
material_path = 'Materials["Upper Soil"]'

# The unit weight property of that material
unit_weight_path = 'Materials["Upper Soil"].UnitWeight'

# The first region's material assignment
region_material_path = 'CurrentAnalysis.GeometryItems.Regions[1].Material'
```

**Path syntax rules:**
- String literals require double-quote encapsulation within brackets: ["Object Name"]
- Array indexing uses numeric indices: [1], [2], etc.
- Property access uses dot notation: Object.Property
- Array indexing uses 1-based numbering (not 0-based)

When unsure of the exact path for an object, use the **SketchText** feature in the GeoStudio UI. SketchText displays a tree-like text view of a project's structure, showing object names and their hierarchical relationships.  While displayed paths may be context-specific, they provide sufficient information for GSI path construction.

### Analysis Names

Projects may contain multiple analysis objects, thus requiring explicit specification:

```python
analysis_name = "Steady-State Analysis"  # This must match exactly
```

**How to find analysis names:** Look at the analysis tree in the GeoStudio project - the names there are what should be used in the python scripts.

---

## Section 4: Reading Information (GET Operations)

### The Concept of Querying

Query operations retrieve information from project objects through structured request-response patterns. Operations follow a consistent three-step process:

1. Creating a "Get Request" specifying the targert information 
2. Executing the request with the project object
3. Receiving and processing the response

### Basic Query Implementation
Material property query example:

```python
import os
from google.protobuf.json_format import MessageToDict
import gsi

def query_material_example():
    project = None
    try:
        project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
        
        # Step 1: Create a request for material properties
        get_request = gsi.GetRequest(
            analysis="Steady-State Analysis",  # Which analysis to look in
            object='Materials["Upper Soil"]'   # Which material to query
        )
        
        # Step 2: Send the request and get the response
        response = project.Get(get_request)
        
        # Step 3: Convert the response to readable format
        material_data = MessageToDict(response.data.struct_value)
        
        # Step 4: Display the results
        print("Material Properties:")
        for property_name, value in material_data.items():
            print(f"  {property_name}: {value}")
            
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        if project is not None:
            project.Close()

if __name__ == '__main__':
    query_material_example()
```

**What this does:**
1. Opens the project
2. Asks for all properties of the "Upper Soil" material
3. Converts the response object into a more readable Python dictionary
4. Prints each property and its value

### Understanding the Response

Query responses contain complete property collections for requested objects. Material responses typically include:
```
Material Properties:
  Name: Upper Soil
  UnitWeight: 18.0
  SlopeModel: MohrCoulomb
  Cohesion: 5.0
  Phi: 25.0
```

### Querying Specific Properties

Instead of retrieving all properties, it's possible to extract just one:

```python
# Get just the unit weight
get_request = gsi.GetRequest(
    analysis="Steady-State Analysis",
    object='Materials["Upper Soil"].UnitWeight'  # Note the .UnitWeight at the end
)

response = project.Get(get_request)
unit_weight = response.data.number_value  # For single numbers
print(f"Unit weight: {unit_weight}")
```

**When to use which approach:**
- Query the whole object to see everything
- Query specific properties to see one value

### Using Formatted Strings for Cleaner Code

When building object paths, it's common to need to insert variable values (like material names or analysis names) into a string. Python's **f-strings** are the most modern and readable way to do this. They allow for embedding expressions directly inside string literals.

This makes code much cleaner and easier to work with, especially for complex object paths.

**Example with f-strings:**

```python
analysis_name = "WS Base Material"
material_name = "Upper Soil"

# Build the object path using f-strings
material_path = f'Materials["{material_name}"]'
unit_weight_path = f'Materials["{material_name}"].UnitWeight'
analysis_path = f'Analyses["{analysis_name}"]'

# A more complex example
piezo_path = f'CurrentAnalysis.Objects.PiezometricSurfaces["{material_name} - Piezometric Line"]'
```

**F-string implementation provides:**

  - **Readability**: The code is more intuitive to read. Final string structure and where the variables are inserted can be identified at a glance.
  - **Maintainability**: Changing the variable or the string structure is a simple, single-line edit.
  - **Efficiency**: F-strings are often faster than older string formatting methods.

---

## Section 5: Modifying Information (SET Operations)

### The Concept of Setting

Set operations modify project object properties through structured value assignment. Operations implement a standardized pattern for property modification with immediate effect application.

### Changing a Single Property
Unit weight modification example:

```python
def change_unit_weight_example():
    project = None
    try:
        project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
        
        # Create a request to change unit weight to 20.0
        set_request = gsi.SetRequest(
            analysis="Steady-State Analysis",
            object='Materials["Upper Soil"].UnitWeight',
            data=gsi.Value(number_value=20.0)  # The new value
        )
        
        # Send the request
        project.Set(set_request)
        print("Unit weight changed to 20.0")
        
        # Let's verify the change by querying it back
        get_request = gsi.GetRequest(
            analysis="Steady-State Analysis",
            object='Materials["Upper Soil"].UnitWeight'
        )
        response = project.Get(get_request)
        new_value = response.data.number_value
        print(f"Verified new unit weight: {new_value}")
        
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        if project is not None:
            project.Close()
```

Set operation components:
1. SetRequest construction with target object and new value specification
2. gsi.Value wrapper for data type specification
3. Immediate value application in project
4. Optional verification through subsequent query

### Different Data Types

The Scripting API handles different types of data:

```python
# Numbers (like unit weight, cohesion, etc.)
number_data = gsi.Value(number_value=18.5)

# Text (like material names, model types)
text_data = gsi.Value(string_value="MohrCoulomb")

# True/False values
boolean_data = gsi.Value(bool_value=True)
```

### Changing Multiple Properties at Once

It's possible to change several properties of the same object:

```python
def change_multiple_properties_example():
    project = None
    try:
        project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
        
        # First, get the current properties
        get_request = gsi.GetRequest(
            analysis="Steady-State Analysis",
            object='Materials["Upper Soil"]'
        )
        current_response = project.Get(get_request)
        current_properties = current_response.data.struct_value
        
        # Update the properties we want to change
        current_properties.update({
            "UnitWeight": 20.0,
            "Cohesion": 10.0,
            "Phi": 30.0
        })
        
        # Create the new data object
        new_data = gsi.Value()
        new_data.struct_value.update(current_properties)
        
        # Send the update
        set_request = gsi.SetRequest(
            analysis="Steady-State Analysis",
            object='Materials["Upper Soil"]',
            data=new_data
        )
        project.Set(set_request)
        print("Multiple properties updated successfully")
        
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        if project is not None:
            project.Close()
```

**Why this approach:**
- Gets all current properties first
- Updates only the ones needing to be change
- Preserves all other properties unchanged

---

## Section 6: Running Analyses

### Why Run Analyses Programmatically?

Once the model is set up (materials, boundary conditions, etc.), it must be solved to get results. Running analyses through the API is useful for:

- Automating repetitive solve operations
- Running multiple scenarios in sequence
- Ensuring analyses are solved after making changes
- Running sensitivity analyses
- Running parameter estimation analyses

### Important Note About Geometry Changes
When geometric modifications are applied through the API, finite element models require re-meshing to reflect changes in the computational domain. The API performs an automatic save operation when SolveAnalyses is called to ensure data consistency, and automatic re-meshing occurs during save operations when the mesh is detected as stale, consistent with GeoStudio interface behavior.

#### What this means for scripts:
- Material property changes and boundary condition changes work as expected
- Geometric modifications require either manual re-meshing through the GeoStudio interface or through the solve process, shown in the section below.
- Mesh verification is essential following geometric changes to ensure geometric modifications are properly reflected

### The Solve Process

Solving an analysis is straightforward:

```python
def solve_analysis_example():
    project = None
    try:
        project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
        
        # Solve the analysis
        analysis_name = "Steady-State Analysis"

        # The analyses argument must always be a list. 
        # If you want to solve just one analysis, wrap its name in square brackets.
        solve_request = gsi.SolveAnalysesRequest(analyses=[analysis_name])  
        project.SolveAnalyses(solve_request)
        
        print(f"Analysis '{analysis_name}' solved successfully!")
        
    except Exception as e:
        print(f"Error solving analysis: {e}")
        
    finally:
        if project is not None:
            project.Close()
```

**What happens during solving:**
- GeoStudio runs the numerical calculations
- Results are computed and stored
- The process can take anywhere from seconds to hours depending on model complexity

### Handling Dependent Analyses
For projects containing interdependent analyses (for example, a transient analysis that uses results from a steady-state analysis), automatic dependency resolution can be enabled using the solve_dependencies parameter:

```python
solve_request = gsi.SolveAnalysesRequest(
    analyses=[analysis_name],
    solve_dependencies=True
)
```
This will solve the specified analysis and any analyses that depend on it. Note that the solve_dependencies parameter defaults to True.

### Interaction with StepNumber
Analysis execution can be limited to specific steps using the step_number parameter. This is especially useful for time-dependent analyses: 

```Python

solve_request = gsi.SolveAnalysesRequest(
    analyses=[analysis_name],
    step_number=5
)
```
When solve_dependencies is True, all dependent analyses will also be solved up to the specified step_number (if applicable). If step_number is omitted, all steps will be solved.

### Solving Multiple Analyses

The Scripting API allows for solving several analyses at once:

```python
# Solve multiple analyses in sequence
analyses_to_solve = ["Steady-State Analysis", "Transient Analysis"]
solve_request = gsi.SolveAnalysesRequest(analyses=analyses_to_solve)
project.SolveAnalyses(solve_request)
```

### Checking if Solve Was Successful

The solve operation will raise an error if something goes wrong, but it's good practice to check:

```python
def robust_solve_example():
    project = None
    try:
        project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
        
        analysis_name = "Steady-State Analysis"
        print(f"Starting solution of '{analysis_name}'...")
        
        solve_request = gsi.SolveAnalysesRequest(analyses=[analysis_name])
        project.SolveAnalyses(solve_request)
        
        print("Solution completed successfully!")
        return True
        
    except Exception as e:
        print(f"Solution failed: {e}")
        return False
        
    finally:
        if project is not None:
            project.Close()
```

---

## Section 7: Basic Results Extraction

### Understanding Results in GeoStudio

After solving an analysis, GeoStudio stores results in different tables, which can be thought of as categories for the output data. When extracting results using the API, the table to query and which specific data is needed from that table must be specified.

- **Nodes**:  This table contains results associated with the mesh nodes (coordinates, pressure heads, temperatures)
- **Elements**: This table holds results for the elements of the mesh (stresses, flows, gradients)
- **Global**: This table stores single, summary-level results for the entire analysis (factor of safety, total flow, etc.)

### Loading Results First

Results must be explicitly loaded into memory before extraction:

```python
def load_results_example():
    project = None
    try:
        project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
        
        analysis_name = "Steady-State Analysis"
        
        # Load the results into memory
        load_request = gsi.LoadResultsRequest(analysis=analysis_name)
        project.LoadResults(load_request)
        print("Results loaded successfully")
        
    except Exception as e:
        print(f"Error loading results: {e}")
        
    finally:
        if project is not None:
            project.Close()
```

**Why load results separately:** Results files can be large, so GeoStudio only loads them into memory when requested.

### Extracting Simple Results

Example to extract coordinates and pressure heads from nodes:

```python
import numpy as np

def extract_basic_results():
    project = None
    try:
        project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
        
        analysis_name = "Steady-State Analysis"
        
        # Load results
        load_request = gsi.LoadResultsRequest(analysis=analysis_name)
        project.LoadResults(load_request)
        
        # Query pressure head results
        query_request = gsi.QueryResultsRequest(
            analysis=analysis_name,
            step=1,  # Usually 1 for steady-state
            table=gsi.ResultType.Nodes,  # We want nodal results
            dataparams=[gsi.DataParamType.eWaterPressureHead]  # Parameter we want to query
        )
        
        response = project.QueryResults(query_request)
        pressure_values = response.results[gsi.DataParamType.eWaterPressureHead].values
        
        # Convert to numpy array for easier analysis
        pressures = np.array(pressure_values)
        
        print(f"Retrieved {len(pressures)} pressure head values")
        print(f"Min pressure: {np.min(pressures):.2f}")
        print(f"Max pressure: {np.max(pressures):.2f}")
        print(f"Average pressure: {np.mean(pressures):.2f}")
        
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        if project is not None:
            project.Close()
```

### Getting Multiple Result Types

The API also allows for extracting several result parameters at once:

```python
def extract_coordinates_and_pressures():
    project = None
    try:
        project = gsi.OpenProject(r"C:\Path\To\Your\Project.gsz")
        
        analysis_name = "Steady-State Analysis"
        
        # Load results
        load_request = gsi.LoadResultsRequest(analysis=analysis_name)
        project.LoadResults(load_request)
        
        # Query multiple parameters
        data_params = [
            gsi.DataParamType.eXCoord,           # X coordinates
            gsi.DataParamType.eYCoord,           # Y coordinates  
            gsi.DataParamType.eWaterPressureHead # Pressure heads
        ]
        
        query_request = gsi.QueryResultsRequest(
            analysis=analysis_name,
            step=1,
            table=gsi.ResultType.Nodes,
            dataparams=data_params
        )
-        response = project.QueryResults(query_request)
        
        # Extract each parameter
        x_coords = np.array(response.results[gsi.DataParamType.eXCoord].values)
        y_coords = np.array(response.results[gsi.DataParamType.eYCoord].values)
        pressures = np.array(response.results[gsi.DataParamType.eWaterPressureHead].values)
        
        print(f"Data points: {len(x_coords)}")
        print(f"X range: {np.min(x_coords):.1f} to {np.max(x_coords):.1f}")
        print(f"Y range: {np.min(y_coords):.1f} to {np.max(y_coords):.1f}")
        print(f"Pressure range: {np.min(pressures):.2f} to {np.max(pressures):.2f}")
        
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
        if project is not None:
            project.Close()
```

---

## Section 8: Putting It All Together

### A Complete Workflow Example
Here is an example of a script that demonstrates the full workflow: open project, modify material, solve, and extract results.

```python
import os
import numpy as np
from google.protobuf.json_format import MessageToDict
import gsi

def complete_workflow_example():
    """
    A complete example showing:
    1. Open project
    2. Query current material properties  
    3. Modify material properties
    4. Solve analysis
    5. Extract and analyze results
    """
    project = None
    try:
        print("=== Complete Workflow Example ===\n")
        
        # 1. Open Project
        project_path = r"C:\Path\To\Your\Project.gsz"  # Change this!
        project = gsi.OpenProject(project_path)
        print("✓ Project opened")
        
        analysis_name = "Steady-State Analysis"  # Change if needed
        material_name = "Upper Soil"              # Change if needed
        
        # 2. Query Current Properties
        print("\n--- Current Material Properties ---")
        get_request = gsi.GetRequest(
            analysis=analysis_name,
            object=f'Materials["{material_name}"]'
        )
        response = project.Get(get_request)
        current_props = MessageToDict(response.data.struct_value)
        
        print(f"Material: {material_name}")
        for prop, value in current_props.items():
            print(f"  {prop}: {value}")
        
        # 3. Modify Properties
        print("\n--- Modifying Properties ---")
        old_unit_weight = current_props.get("UnitWeight", "Not found")
        new_unit_weight = 19.0
        
        set_request = gsi.SetRequest(
            analysis=analysis_name,
            object=f'Materials["{material_name}"].UnitWeight',
            data=gsi.Value(number_value=new_unit_weight)
        )
        project.Set(set_request)
        print(f"✓ Changed unit weight from {old_unit_weight} to {new_unit_weight}")
        
        # 4. Solve Analysis
        print("\n--- Solving Analysis ---")
        solve_request = gsi.SolveAnalysesRequest(analyses=[analysis_name])
        project.SolveAnalyses(solve_request)
        print("✓ Analysis solved successfully")
        
        # 5. Load Results
        print("\n--- Loading Results ---")
        load_request = gsi.LoadResultsRequest(analysis=analysis_name)
        project.LoadResults(load_request)
        print("✓ Results loaded")
        
        # 6. Extract Results
        print("\n--- Extracting Results ---")
        query_request = gsi.QueryResultsRequest(
            analysis=analysis_name,
            step=1,
            table=gsi.ResultType.Nodes,
            dataparams=[
                gsi.DataParamType.eXCoord,
                gsi.DataParamType.eYCoord,
                gsi.DataParamType.eWaterPressureHead
            ]
        )
        
        response = project.QueryResults(query_request)
        
        x_coords = np.array(response.results[gsi.DataParamType.eXCoord].values)
        y_coords = np.array(response.results[gsi.DataParamType.eYCoord].values)
        pressures = np.array(response.results[gsi.DataParamType.eWaterPressureHead].values)
        
        # 7. Analyze Results
        print("\n--- Results Summary ---")
        print(f"Total data points: {len(pressures)}")
        print(f"Pressure range: {np.min(pressures):.2f} to {np.max(pressures):.2f}")
        print(f"Average pressure: {np.mean(pressures):.2f}")
        print(f"Standard deviation: {np.std(pressures):.2f}")
        
        print("\n=== Workflow Completed Successfully ===")
        
    except Exception as e:
        print(f"\nError in workflow: {e}")
        print("Check your project path, analysis name, and material name.")
        
    finally:
        if project is not None:
            project.Close()
            print("\nProject closed safely")

if __name__ == '__main__':
    complete_workflow_example()
```

### What This Example Teaches

This complete example demonstrates:

1. Safe opening and closing
2. Getting current material properties
3. Changing a material parameter
4. Solving the updated model
5. Getting numerical results
6. Basic statistical analysis of results

### Next Steps for Learning

Upon understanding basic workflow patterns, advanced implementations may include:

1. **Modifying different properties**: Try changing cohesion, friction angle, permeability
2. **Working with boundary conditions**: Assign different BCs to regions
3. **Extracting different results**: Try getting velocities, stresses, temperatures
4. **Creating parameter studies**: Loop through different values automatically
5. **Processing multiple projects**: Apply the same changes to many files

A collection of pre-made scripts implementing various common workflows is available at: https://aka.bentley.com/GeoStudio.url.ScriptingAPIDocs/1