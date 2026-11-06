import random

from prompt import string as prompt

DESCRIPTION = (
    'Answer "yes" if the number is even, otherwise answer "no".'
)
ROUNDS = 3


def is_even(number):
    return number % 2 == 0


def main():
    print('Welcome to the Brain Games!')
    name = prompt('May I have your name? ')
    print('Hello, {0}!'.format(name))
    print(DESCRIPTION)
    for _ in range(ROUNDS):
        number = random.randint(1, 100)
        print('Question: {0}'.format(number))
        answer = prompt('Your answer: ')
        correct_answer = 'yes' if is_even(number) else 'no'
        if answer == correct_answer:
            print('Correct!')
        else:
            wrong = (
                "'{0}' is wrong answer ;(. "
                "Correct answer was '{1}'."
            ).format(answer, correct_answer)
            print(wrong)
            print("Let's try again, {0}!".format(name))
            return
    print('Congratulations, {0}!'.format(name))
