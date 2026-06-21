cmake_minimum_required(VERSION 3.25)
project(py_omni_python_sqlalchemy)

include(../PythonPackage.cmake)

add_python_package(py_omni_python_sqlalchemy
        PACKAGE_NAME omni_python_sqlalchemy
        VERSION 0.1.0
        SOURCES
        src/omni_python_sqlalchemy/__init__.py
        )
