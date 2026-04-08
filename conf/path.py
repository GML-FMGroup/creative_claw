# coding: utf-8
# @author: zyh
# @file: path.py
import os

PROJECT_PATH = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
) 

CONF_ROOT = os.path.join(PROJECT_PATH, "conf")  
DATA_ROOT = os.path.join(PROJECT_PATH, "data") 
LOGS_ROOT = os.path.join(PROJECT_PATH, "logs") 
SRC_ROOT = os.path.join(PROJECT_PATH, "src") 
TEST_ROOT = os.path.join(PROJECT_PATH, "unit_test") 
