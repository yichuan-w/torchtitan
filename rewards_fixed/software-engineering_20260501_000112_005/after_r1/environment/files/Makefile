# Makefile for parser project
# This Makefile has issues that prevent parallel builds from succeeding

CC = gcc
LEX = flex
YACC = bison

CFLAGS = -Wall -Wextra -g -I./include -I./lib -I./src
LDFLAGS = 

# Source directories
SRC_DIR = src
LIB_DIR = lib
INC_DIR = include
BUILD_DIR = build

# Generated files
LEXER_C = $(BUILD_DIR)/lexer.c
PARSER_C = $(BUILD_DIR)/parser.c
PARSER_H = $(SRC_DIR)/parser.h

# Source files
MAIN_SRC = $(SRC_DIR)/main.c
AST_SRC = $(SRC_DIR)/ast.c
UTILS_SRC = $(LIB_DIR)/utils.c

# Object files
MAIN_OBJ = $(BUILD_DIR)/main.o
LEXER_OBJ = $(BUILD_DIR)/lexer.o
PARSER_OBJ = $(BUILD_DIR)/parser.o
AST_OBJ = $(BUILD_DIR)/ast.o
UTILS_OBJ = $(BUILD_DIR)/utils.o

# Libraries
UTILS_LIB = $(BUILD_DIR)/libutils.a

# Target
TARGET = parser

# Default target
all: $(TARGET)

# Create build directory
$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

# Link the final executable
$(TARGET): $(MAIN_OBJ) $(LEXER_OBJ) $(PARSER_OBJ) $(AST_OBJ) $(UTILS_LIB)
	$(CC) $(CFLAGS) -o $@ $(MAIN_OBJ) $(LEXER_OBJ) $(PARSER_OBJ) $(AST_OBJ) $(UTILS_LIB) $(LDFLAGS)

# Build static library
$(UTILS_LIB): $(UTILS_OBJ)
	ar rcs $@ $<

# Compile main
$(MAIN_OBJ): $(MAIN_SRC) $(INC_DIR)/config.h $(INC_DIR)/ast.h
	$(CC) $(CFLAGS) -c $< -o $@

# Compile AST
$(AST_OBJ): $(AST_SRC) $(INC_DIR)/ast.h
	$(CC) $(CFLAGS) -c $< -o $@

# Compile lexer - Note: lexer.c includes parser.h (generated header)
$(LEXER_OBJ): $(LEXER_C)
	$(CC) $(CFLAGS) -c $< -o $@

# Generate lexer
$(LEXER_C): $(SRC_DIR)/lexer.l
	$(LEX) -o $@ $<

# Compile parser
$(PARSER_OBJ): $(PARSER_C) $(INC_DIR)/ast.h
	$(CC) $(CFLAGS) -c $< -o $@

# Generate parser
$(PARSER_C) $(PARSER_H): $(SRC_DIR)/parser.y
	$(YACC) -d -o $(PARSER_C) --defines=$(PARSER_H) $<

# Compile utils
$(UTILS_OBJ): $(UTILS_SRC) $(LIB_DIR)/utils.h
	$(CC) $(CFLAGS) -c $< -o $@

# Clean
clean:
	rm -rf $(BUILD_DIR) $(TARGET) $(PARSER_H)

# Phony targets
.PHONY: all clean

# Include auto-generated dependencies
-include $(BUILD_DIR)/*.d
