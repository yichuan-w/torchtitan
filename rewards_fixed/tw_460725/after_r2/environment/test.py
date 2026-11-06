from InquirerPy import inquirer


print("What is your name?")
name = input()
print(f"Hewwo {name}! Nice to meet you~ ^w^")
animal = inquirer.select(
    message="What is your favorite animal?",
    choices=["Cat", "Dog", "Fox"],
    wrap_lines=False,
).execute()
print(f"Woah~, the {animal} is a pretty cool animal!")
