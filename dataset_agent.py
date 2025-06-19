import os
import sys
import pandas as pd
from together import Together
from typing import Optional, List, Dict, Any, Tuple, Callable
import tiktoken

# Check for Together API token
if "TOGETHER_API_KEY" not in os.environ:
    print("Error: TOGETHER_API_KEY not found in environment variables")
    print("Please set it with: export TOGETHER_API_KEY=your-token-here")
    sys.exit(1)

# Defines Agent
class DatasetAgent:
    def __init__(self, llm):
        self.llm = llm
        self.tokenizer = tiktoken.get_encoding("cl100k_base")
        self.supported_formats = {
            '.csv': ('CSV', pd.read_csv),
            '.tsv': ('TSV', lambda x: pd.read_csv(x, sep='\t')),
            '.txt': ('TSV', lambda x: pd.read_csv(x, sep='\t')),
            '.xlsx': ('XLSX', pd.read_excel),
            '.xls': ('XLS', pd.read_excel),
            '.parquet': ('PARQUET', pd.read_parquet),
            '.json': ('JSON', pd.read_json),
            '.feather': ('FEATHER', pd.read_feather)
        }
        self.current_file = None
        self.default_output_names = {
            'plot': 'output_plot.png',
            'data': 'transformed_data.csv',
            'excel': 'transformed_data.xlsx',
            'json': 'transformed_data.json'
        }

    def validate_file_path(self, file_path: str) -> Tuple[bool, str]:
        # Validate if the file exists and is in a supported format
        if not os.path.exists(file_path):
            return False, f"File not found: {file_path}"
        
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.supported_formats:
            return False, f"Unsupported file format: {ext}. Supported formats: {', '.join(self.supported_formats.keys())}"
        
        return True, ""

    def check_file_extension(self, file_path: str) -> Tuple[str, Callable]:
        # Return the file format and appropriate reader function
        ext = os.path.splitext(file_path)[1].lower()
        return self.supported_formats.get(ext, ("Unknown", None))

    def validate_generated_code(self, code: str) -> Tuple[bool, str]:
        # Validation of generated code for security and correctness
        if not code or not isinstance(code, str):
            return False, "No code was generated or invalid code type"

        # Strip any potential markdown formatting
        if code.startswith("```python"):
            code = code.split("```python")[1]
        if code.endswith("```"):
            code = code.rsplit("```", 1)[0]
        code = code.strip()

        # Forbidden terms but allow subprocess for package installation
        forbidden_terms = ['eval', 'os.system', 'import sys', '__import__']
        required_terms = ['import pandas', 'importlib.util']
        
        # Check for forbidden terms
        for term in forbidden_terms:
            if term in code and 'subprocess.check_call([sys.executable, \'-m\', \'pip\'' not in code:
                return False, f"Code contains forbidden term: {term}"
        
        # Check for required terms
        for term in required_terms:
            if term not in code:
                return False, f"Code is missing required term: {term}"
        
        # Check for basic syntax errors
        try:
            compile(code, '<string>', 'exec')
        except SyntaxError as e:
            print("\nDebug - Raw code received:")
            print(code)
            print("\nSyntax error details:")
            print(str(e))
            return False, f"Code contains syntax error: {str(e)}"
        
        return True, code

    def get_analysis_prompt(self, datasetColumns: list, datasetRows: list, datasetTypes: list, query: str) -> str:
        return f"""Given a dataset with the following structure, write Python code to {query}

Dataset Information:
- Columns: {datasetColumns}
- Sample Data (first 5 rows): {datasetRows[:5]}
- Column Types: {datasetTypes}
- Total Rows: {len(datasetRows)}

Requirements:
1. Start with a package management section that:
   - Lists all required packages in a list variable
   - Checks if each package is installed using importlib.util.find_spec
   - Installs missing packages using pip (subprocess.check_call)
   - Only then imports the required packages
2. Use pandas for data manipulation
3. Read the dataset from '{self.current_file}'
4. For any outputs:
   - Plots/graphs save to '{self.default_output_names['plot']}'
   - Transformed data save to '{self.default_output_names['data']}'
   (Unless specific output names are provided in the query)
5. Include error handling
6. Add comments explaining each section
7. Use full row indices for any row-specific operations

Example package management format:
```python
import importlib.util
import subprocess
import sys

# List of required packages
required_packages = ['pandas', 'matplotlib', 'seaborn']  # Add any packages you need

# Check and install missing packages
for package in required_packages:
    if importlib.util.find_spec(package) is None:
        print(f"{'package'} not found. Installing...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'package'])
    else:
        print(f"{'package'} is already installed.")

# Now import the required packages
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
```

Return only the Python code without any additional text or markdown."""

    def get_code(self, file_path: str, query: str):
        try:
            # Validate file path
            is_valid, error_message = self.validate_file_path(file_path)
            if not is_valid:
                print(error_message)
                return None

            self.current_file = file_path
            
            # Read in dataset structure from file
            file_format, reader_func = self.check_file_extension(file_path)
            if reader_func is None:
                return None

            df = reader_func(file_path)
            
            datasetColumns = df.columns.tolist()
            datasetRows = df.values.tolist()  # Get all rows
            datasetTypes = df.dtypes.tolist()

            prompt = self.get_analysis_prompt(datasetColumns, datasetRows, datasetTypes, query)
            
            # Get code from LLM
            code_analysis = self.llm.invoke(prompt)

            # Validate generated code
            is_valid, result = self.validate_generated_code(code_analysis)
            if not is_valid:
                print(f"Generated code validation failed: {result}")
                return None

            # Save the validated and cleaned code
            with open('generated_code.py', 'w') as file:
                file.write(result)

            print("\nCleaned and validated code saved to generated_code.py")

            return result

        except Exception as e:
            print(f"Error in get_code: {str(e)}")
            return None

class TogetherEndpoint:
    def __init__(self, model_id: str, temperature: float = 0.2, max_tokens: int = 500):
        self.client = Together()
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        
    def invoke(self, prompt: str, **kwargs) -> str:
        messages = [{
            "role": "user",
            "content": prompt
        }]
        
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error with Together API: {str(e)}")
            raise

# Create agent, called by main()
def create_agents():
    
    # Initialize LLM with Together model
    llm = TogetherEndpoint(
        model_id="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        temperature=0.2,
        max_tokens=500
    )
    
    dataset_agent = DatasetAgent(llm)
    
    return dataset_agent

# calls create_agents() and then enters a while loop to allow the user to choose the mode of operation
def main():
    dataset_agent = create_agents()
    
    while True:
        query = input("\nEnter your data visualization/manipulation request (or 'q' to quit): ")
        
        if query.lower() == 'q':
            break

        dataset = input("\nEnter the path to the dataset file: ")

        try:
            print("\nGenerating and executing code...")
            code = dataset_agent.get_code(dataset, query)
            
            if code:
                # Execute the generated code in a try-except block
                try:
                    exec(code, {'__file__': dataset})
                    print("\nCode executed successfully!")
                except Exception as e:
                    print(f"\nError executing generated code: {str(e)}")
            else:
                print("\nNo code was generated due to an error.")
                
        except Exception as e:
            print(f"\nError: {str(e)}")

if __name__ == "__main__":
    main() 