#!/usr/bin/env python
import json
import re
import random
import string
from pathlib import Path
import llm
from datetime import datetime
import time

SHOW_AVAILABLE_CLUES = True
PROMPT_TEMPLATE = """
Solve this nested crossword puzzle. You can only solve clues that are fully revealed (no nested brackets). Answers are usually one word.

Current Puzzle State:
{puzzle_state}

{available_clues_section}
Previous failed attempts:
{failed_attempts}

Commands:
- try <answer>    Attempt to solve a clue
- peek <clue>     Reveal first letter of a clue's answer
- reveal <clue>   Give up and show answer for a clue

Peeking and revealing will lose you points but are better than not getting an answer and losing the game. Repeating an answer you've already sent will only lose you points.  

Respond only with one command. When trying an answer, do not include the clue, just the answer. What's your move?"""


class PuzzleEvaluation:

    def __init__(self, model_name=None, key=None):
        self.model_name = model_name or self.select_model()
        self.key = key or self.select_or_create_key()
        self.score_file = Path(f"scores/{self.model_name}.{self.key}.json")

        # Initialize or load state
        if self.score_file.exists():
            self.load_state()
        else:
            self.initialize_new_state()

        self.llm_model = llm.get_model(self.model_name)

    def select_model(self):
        """Let user choose from installed LLM models"""
        models = list(llm.get_models())

        if not models:
            print("No models installed! Install one with 'llm install'")
            exit(1)

        print("Available models:")
        for i, model in enumerate(models, 1):
            print(f"{i}. {model.model_id}")

        while True:
            try:
                choice = int(input("\nSelect model (1-{}): ".format(len(models))))
                if 1 <= choice <= len(models):
                    return models[choice - 1].model_id
                print("Invalid selection")
            except ValueError:
                print("Please enter a number")

    def select_or_create_key(self):
        """Manage score file selection/creation for a model"""
        scores_dir = Path("scores")
        scores_dir.mkdir(exist_ok=True)

        # Find existing keys
        pattern = f"{self.model_name}.*.json"
        existing_files = list(scores_dir.glob(pattern))
        existing_keys = [f.stem.split('.')[-1] for f in existing_files]

        # Show existing options
        if existing_keys:
            print(f"Existing score files for {self.model_name}:")
            for i, key in enumerate(existing_keys, 1):
                print(f"{i}. {key}")
            print("n. Create new score file")
        else:
            print(f"No existing score files for {self.model_name}")

        # Get user choice
        while True:
            choice = input("Enter choice (1-n/n/new): ").strip().lower()

            if choice in ('n', 'new'):
                # Generate unique 6-character token
                while True:
                    new_key = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
                    if new_key not in existing_keys:
                        print(f"Okay, new key is {new_key}")
                        return new_key
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(existing_keys):
                    return existing_keys[idx]
                print("Invalid selection")
            else:
                print("Please enter a valid choice")

    def initialize_new_state(self):
        self.state = {
            'model': self.model_name,
            'peeks': 0,
            'reveals': 0,
            'wrong_guesses': 0,
            'repeats': 0,
            'puzzles_won': 0,
            'puzzles_attempted': [],
            'current_puzzle': None,
            'puzzle_state': None,
            'failed_attempts': [],
            'remaining_puzzles': self.load_random_puzzles()
        }

    def load_state(self):
        with open(self.score_file, 'r') as f:
            self.state = json.load(f)

        # Load fresh puzzles if we've exhausted the previous ones
        if not self.state['remaining_puzzles']:
            self.state['remaining_puzzles'] = self.load_random_puzzles()

    def save_state(self):
        self.score_file.parent.mkdir(exist_ok=True)
        with open(self.score_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def load_random_puzzles(self, file_path='puzzles', count=1):
        try:
            with open(file_path) as f:
                all_puzzles = json.load(f)

                # If JSON is an object with "puzzles" array
                if isinstance(all_puzzles, dict) and 'puzzles' in all_puzzles:
                    all_puzzles = all_puzzles['puzzles']

                if len(all_puzzles) < count:
                    raise ValueError(f"Only {len(all_puzzles)} puzzles found, need at least {count}")

                return random.sample(all_puzzles, count)

        except FileNotFoundError:
            print(f"Error: {file_path} not found")
            exit(1)
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON in {file_path}")
            exit(1)
        except ValueError as e:
            print(e)
            exit(1)

    def get_available_clues(self):
        puzzle_state = self.state['puzzle_state']
        available = []
        for clue in self.state['current_puzzle']['solutions'].keys():
            if clue in puzzle_state:
                available.append(clue)
        return available

    def run(self):
        while True:
            if not self.state['current_puzzle']:
                self.load_next_puzzle()

            result = self.run_single_puzzle()
            self.update_score(result)
            self.save_state()

            if result['completed']:
                print(f"Puzzle solved! Moving to next puzzle.")
                self.state['current_puzzle'] = None
            else:
                print(f"Puzzle unsolved. Saving progress.")
                break

    def load_next_puzzle(self):
        if not self.state['remaining_puzzles']:
            self.state['remaining_puzzles'] = self.load_random_puzzles()

        self.state['current_puzzle'] = self.state['remaining_puzzles'].pop()
        self.state['puzzle_state'] = self.state['current_puzzle']['initialPuzzle']
        self.state['failed_attempts'] = []

    def run_single_puzzle(self):
        puzzle = self.state['current_puzzle']
        max_guesses = 150

        for _ in range(max_guesses):
            if self.check_completion(puzzle):
                return {'completed': True}

            available = self.get_available_clues()
            if not available:
                return {'completed': False}

            prompt = self.build_prompt(available)
            response = self.get_llm_response(prompt)

            match = re.match(r"(try|peek|reveal)\s+(.+)", response, re.IGNORECASE)
            if not match:
                print("Invalid command format")
                continue

            cmd, target = match.groups()
            cmd = cmd.lower()
            # Process command
            if cmd == "try":
                correct = False
                for clue in available:
                    if puzzle['solutions'][clue].lower() == target.lower():
                        self.state['puzzle_state'] = self.state['puzzle_state'].replace(f"[{clue}]", puzzle['solutions'][clue], 1)
                        correct = True
                        print(f"Found {target}")
                        break
                if not correct:
                    if target in self.state['failed_attempts']:
                        self.state['repeats'] += 1
                    else:
                        self.state["wrong_guesses"] += 1
                        self.state['failed_attempts'].append(target)

            elif cmd == "peek":
                if target in available:
                    self.state["peeks"] += 1
                    # TODO: actually tell the LLM about peeked letters
                    first_letter = puzzle['solutions'][target][0]
                    print(f"Peek result: {first_letter}")
                else:
                    print("Invalid clue for peeking")

            elif cmd == "reveal":
                if target in available:
                    self.state["reveals"] += 1
                    puzzle['current_state'] = puzzle['current_state'].replace(
                        f"[{target}]", puzzle['solutions'][target], 1
                    )
                else:
                    print("Invalid clue for reveal")

            else:
                print("Unknown command")

            self.save_state()

        return {'completed': False}

    def build_prompt(self, available_clues):
        clues_list = "\n".join(f"- {clue}" for clue in available_clues)
        available_section = f"Available Clues:\n{clues_list}" if SHOW_AVAILABLE_CLUES else ""

        return PROMPT_TEMPLATE.format(
            puzzle_state=self.state['puzzle_state'],
            available_clues_section=available_section,
            failed_attempts=self.state['failed_attempts']
        )

    def get_llm_response(self, prompt):
        consecutive_failures = 0
        while True:
            try:
                response = self.llm_model.prompt(prompt).text().strip()
                print(f"LLM Response: {response}")
                return response
            except llm.errors.ModelError as e:
                if consecutive_failures >= 5:
                    print("5 failures")
                    raise e
                time_to_sleep = 1 + 5 * consecutive_failures
                print(f"problem, pausing for {time_to_sleep}")
                time.sleep(time_to_sleep)
                consecutive_failures += 1

    def update_score(self, result):
        self.state['puzzles_attempted'].append({
            'date': datetime.now().isoformat(),
            'result': result
        })
        if result['completed']:
            self.state['puzzles_won'] += 1

    def check_completion(self, puzzle):
        return self.state['puzzle_state'] == puzzle['puzzleSolution']


if __name__ == "__main__":
    evaluation = PuzzleEvaluation()
    evaluation.run()
    print("\nFinal Score:")
    print(json.dumps(evaluation.state, indent=2))