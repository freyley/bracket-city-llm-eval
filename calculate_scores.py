#!/usr/bin/env python 
import argparse
import json
import os
import statistics
from typing import List, Dict

def calculate_score(data: Dict) -> int:
    """Calculate score based on the provided scoring rules."""
    score = 500
    
    # Deduct for wrong guesses
    score -= data["wrong_guesses"] * 1
    score -= data["repeats"] * 2
    
    # Deduct for peeks
    unique_peeks = len(data["peeks"])
    repeat_peeks = data["repeat_peeks"]
    peek_cost = unique_peeks * 4 + repeat_peeks * 8
    score -= peek_cost
    
    # Deduct for reveals
    unique_reveals = len(data["reveals"])
    repeat_reveals = data["repeat_reveals"]
    reveal_cost = unique_reveals * 15 + repeat_reveals * 45
    score -= reveal_cost

    # Deduct for incomplete puzzle
    if not data["completed"]:
        score -= 500
    
    return score

def process_folder(folder_path: str):
    """Process all JSON files in folder and generate scores.json"""
    scores: List[int] = []
    
    # Process each JSON file
    for filename in os.listdir(folder_path):
        if filename.endswith(".json"):
            file_path = os.path.join(folder_path, filename)
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                scores.append(calculate_score(data))
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error processing {filename}: {str(e)}")
                continue
    
    # Calculate statistics
    stats = {
        "scores": scores,
        "mean": None,
        "median": None,
        "quartiles": []
    }
    
    if scores:
        stats["mean"] = statistics.mean(scores)
        stats["median"] = statistics.median(scores)
        if len(scores) >= 1:
            try:
                q = statistics.quantiles(scores, n=4)
                stats["quartiles"] = [q[0], q[-1]]  # Q1 and Q3
            except statistics.StatisticsError:
                pass
    
    # Write results
    output_path = os.path.join(folder_path, "scores.json")
    with open(output_path, "w") as f:
        json.dump(stats, f, indent=2)
    
    print(f"Processed {len(scores)} files. Results saved to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate LLM puzzle scores")
    parser.add_argument("folder", help="Path to folder containing JSON files")
    args = parser.parse_args()
    
    if not os.path.isdir(args.folder):
        print(f"Error: {args.folder} is not a valid directory")
        exit(1)
        
    process_folder(args.folder)
