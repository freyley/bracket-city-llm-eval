#!/usr/bin/env python
import json
import re
import random
import string
from pathlib import Path
import llm
from datetime import datetime
import time
from tqdm import tqdm

SHOW_AVAILABLE_CLUES = True
PROMPT_TEMPLATE = """
Solve this nested crossword puzzle. You can only solve clues that are fully revealed (no nested brackets). Answers are usually one word.

Current Puzzle State:
{puzzle_state}

{available_clues_section}

Commands:
- try <answer>    Attempt to solve a clue
- peek <clue>     Reveal first letter of a clue's answer
- reveal <clue>   Give up and show answer for a clue
- see             See the current puzzle state

You are being scored on your activities. A perfect score is if you solve the puzzle with no wrong guesses and no peeks or reveals. Get the best score you can.
Wrong guesses, repeating wrong guesses, peeking and revealing all lose you points. 
Revealing loses you the most points - 15 per reveal. 
A peek is worth 4 wrong guesses.
See does not cost you or gain you points.
Repeating an attempt when it didn't work before or repeating a peek or reveal doubles your loss. 

Respond only with one command. When trying an answer, do not include the clue, just the answer. What's your move?"""


class PuzzleEvaluation:

    def __init__(self, model_name, key, puzzle):
        self.model_name = model_name
        self.key = key
        self.initialize_new_state()
        self.state['current_puzzle'] = puzzle[0]
        puzzleDate = puzzle[0]['puzzleDate']
        self.state['puzzle_state'] = self.state['current_puzzle']['initialPuzzle']

        self.score_file = Path(f"scores/{self.model_name}.{self.key}/{puzzleDate}.json")
        self.transcript_file = Path(f"scores/{self.model_name}.{self.key}/{puzzleDate}.transcript")

        self.llm_model = llm.get_model(self.model_name)

    def initialize_new_state(self):
        self.state = {
            'model': self.model_name,
            'peeks': [],
            'completed': False,
            'peek_requests': [],
            'reveal_requests': [],
            'failed_reveals': [],
            'failed_peeks': [],
            'reveals': [],
            'repeat_reveals': 0,
            'repeat_peeks': 0,
            'wrong_guesses': 0,
            'correct': 0,
            'repeats': 0,
            'puzzles_won': 0,
            'puzzles_attempted': [],
            'current_puzzle': None,
            'puzzle_state': None,
            'failed_attempts': [],
        }

    def save_state(self):
        self.score_file.parent.mkdir(exist_ok=True)
        with open(self.score_file, 'w') as f:
            json.dump(self.state, f, indent=2)

    def add_to_transcript(self, addition):
        with open(self.transcript_file, 'a') as f:
            f.write(addition +"\n")

    def get_available_clues(self):
        puzzle_state = self.state['puzzle_state']
        available = []
        for clue in self.state['current_puzzle']['solutions'].keys():
            if clue in puzzle_state:
                available.append(clue)
        return available

    def run(self):
        puzzle = self.state['current_puzzle']
        max_guesses = 150
        chat = self.llm_model.conversation()
        available = self.get_available_clues()
        prompt = self.build_prompt(available)

        for _ in tqdm(range(max_guesses)):
            response = self.get_llm_response(chat, prompt)
            self.add_to_transcript(prompt + "\n" + response)

            if response == 'see':
                prompt = f"""The puzzle is now
{self.state['puzzle_state']}"""
                continue
            match = re.match(r"(try|peek|reveal)\s+(.+)", response, re.IGNORECASE)
            if not match:
                prompt = "I didn't understand that. The available commands are `try <answer>`, `peek <clue>` and `reveal <clue>`"
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
                        self.state['correct'] += 1
                        prompt = f"""{target} was correct! The new puzzle state is 
{self.state['puzzle_state']}
You can now `try`, `peek`, `reveal` the next step or `see` to see the puzzle."""
                        available = self.get_available_clues()
                        break
                if not correct:
                    if target in self.state['failed_attempts']:
                        prompt = f"You already tried {target} and it was wrong then."
                        self.state['repeats'] += 1
                    else:
                        prompt = f"{target} is either not a correct solution or is a solution to a clue that's not currently available."
                        self.state["wrong_guesses"] += 1
                        self.state['failed_attempts'].append(target)

            elif cmd == "peek":
                if target in available:
                    if target in self.state['peeks']:
                        self.state['repeat_peeks'] += 1
                        prompt = "You've already peeked at this."
                    else:
                        self.state["peeks"].append(target)
                        first_letter = puzzle['solutions'][target][0]
                        prompt = f"For {target}, the first letter is {first_letter}."
                else:
                    if target in self.state['failed_peeks']:
                        prompt = "You've already tried to peek at this, and it's not an available clue."
                        self.state['repeat_peeks'] += 1
                    else:
                        prompt = f"I can't peek at {target} because it's not an available clue."
                        self.state['failed_peeks'].append(target)

            elif cmd == "reveal":
                if "[" in target:
                    prompt = f"I cannot reveal {target} because it contains multiple clues. Remember that the brackets signify the place where a clue is, and only the clue within the inner brackets can be tried or revealed."
                    self.state['failed_reveals'] += target
                elif target in available:
                    if target in self.state['reveals']:
                        prompt = "You've already asked to reveal this clue."
                        self.state['repeat_reveals'] += 1
                    else:
                        self.state["reveals"].append(target)
                        self.state['puzzle_state'] = self.state['puzzle_state'].replace(
                            f"[{target}]", puzzle['solutions'][target], 1
                        )
                        available = self.get_available_clues()
                        prompt = f"""The solution to {target} is {puzzle['solutions'][target]}.
The new puzzle state is {self.state["puzzle_state"]}
You can now `try`, `peek`, `reveal` the next step or `see` to see the puzzle.
                    """
                else:
                    if target in self.state['failed_reveals']:
                        prompt = "You've already tried to reveal this, and it's not an available clue."
                        self.state['repeat_reveals'] += 1
                    else:
                        prompt = f"I cannot reveal {target} because it's not currently an available clue."
                    self.state['failed_reveals'].append(target)
            else:
                prompt = f"I don't know how you got here, but {cmd} is an invalid command."

            self.save_state()

            if self.check_completion(puzzle):
                self.state['completed'] = True


    def build_prompt(self, available_clues):
        clues_list = "\n".join(f"- {clue}" for clue in available_clues)
        available_section = f"Available Clues:\n{clues_list}" if SHOW_AVAILABLE_CLUES else ""

        return PROMPT_TEMPLATE.format(
            puzzle_state=self.state['puzzle_state'],
            available_clues_section=available_section,
        )

    def get_llm_response(self, conversation, prompt):
        consecutive_failures = 0
        while True:
            try:
                response = conversation.prompt(prompt).text().strip()
                return response
            except llm.errors.ModelError as e:
                if consecutive_failures >= 5:
                    print("5 failures")
                    raise e
                time_to_sleep = 1 + 5 * consecutive_failures
                print(f"problem, pausing for {time_to_sleep}")
                time.sleep(time_to_sleep)
                consecutive_failures += 1

    def check_completion(self, puzzle):
        return self.state['puzzle_state'] == puzzle['puzzleSolution']


def select_model():
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


def select_or_create_key(model):
    """Manage score file selection/creation for a model. Returns (key, dates_list) tuple."""
    scores_dir = Path("scores")
    scores_dir.mkdir(exist_ok=True)

    existing_keys = []
    key_dates = {}  # Maps keys to their sorted list of dates

    # Find existing directories matching model.key pattern
    for dir_path in scores_dir.glob(f"{model}.*"):
        if dir_path.is_dir():
            parts = dir_path.name.rsplit('.', 1)
            if len(parts) == 2 and parts[0] == model:
                key = parts[1]
                existing_keys.append(key)
                # Collect dates from files in the directory
                dates = set()
                for file_path in dir_path.iterdir():
                    if file_path.is_file() and file_path.suffix in ('.json', '.transcript'):
                        dates.add(file_path.stem)
                key_dates[key] = sorted(dates)

    # Show existing options with date counts
    if existing_keys:
        print(f"Existing score files for {model}:")
        for i, key in enumerate(existing_keys, 1):
            dates = key_dates.get(key, [])
            count = len(dates)
            dates_info = f"{count} dates" if count != 0 else "No dates"
            print(f"{i}. {key} ({dates_info})")
        print("n. Create new score file")
    else:
        print(f"No existing score files for {model}")

    # Get user choice
    while True:
        choice = input("Enter choice (1-n/n/new): ").strip().lower()

        if choice in ('n', 'new'):
            # Generate unique 6-character token
            while True:
                new_key = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
                if new_key not in existing_keys:
                    new_dir = scores_dir / f"{model}.{new_key}"
                    try:
                        new_dir.mkdir(exist_ok=False)
                        print(f"Okay, new key is {new_key}")
                        return new_key, []
                    except FileExistsError:
                        # Race condition, retry
                        continue
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(existing_keys):
                selected_key = existing_keys[idx]
                return selected_key, key_dates.get(selected_key, [])
            print("Invalid selection")
        else:
            print("Please enter a valid choice")


def load_random_puzzle_excluding(dates, file_path='puzzles', count=1):
    try:
        with open(file_path) as f:
            all_puzzles = json.load(f)

            # If JSON is an object with "puzzles" array
            if isinstance(all_puzzles, dict) and 'puzzles' in all_puzzles:
                all_puzzles = all_puzzles['puzzles']

            # Filter out puzzles with dates that are already present
            filtered_puzzles = [puzzle for puzzle in all_puzzles if puzzle['puzzleDate'] not in dates]

            if len(filtered_puzzles) < count:
                raise ValueError(f"Only {len(filtered_puzzles)} eligible puzzles found (after exclusion), need at least {count}")
            return random.sample(filtered_puzzles, count)

    except FileNotFoundError:
        print(f"Error: {file_path} not found")
        exit(1)
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON in {file_path}")
        exit(1)
    except KeyError as e:
        print(f"Error: Malformed puzzle data - missing 'puzzleDate' field")
        exit(1)
    except ValueError as e:
        print(e)
        exit(1)

if __name__ == "__main__":
    model = select_model()
    key, dates = select_or_create_key(model)
    puzzles = load_random_puzzle_excluding(dates, count=10)
    for puzzle in puzzles:
        evaluation = PuzzleEvaluation(model, key, puzzle)
        evaluation.run()

