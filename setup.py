"""
Setup script for fastapi-gradual-throttle.

This is a thin shim kept for editable installs and legacy tooling.
The canonical config lives in pyproject.toml.
"""

from setuptools import setup, find_packages


def read_readme():
    with open("README.md", "r", encoding="utf-8") as f:
        return f.read()


setup(
    name="fastapi-gradual-throttle",
    version="1.0.0",
    description=(
        "FastAPI middleware for gradual request throttling with configurable "
        "delay strategies, strict rate limiting, and per-route overrides"
    ),
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    author="Dhruv Patel",
    author_email="pateldhruvn2004@gmail.com",
    url="https://github.com/DhruvSimform/fastapi_gradual_throttle",
    packages=find_packages(include=["fastapi_gradual_throttle*"]),
    include_package_data=True,
    install_requires=[
        "fastapi>=0.68",
        "starlette>=0.20",
        "pydantic-settings>=2.0",
    ],
    extras_require={
        "redis": ["redis>=4.2"],
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "httpx>=0.24",
            "pytest-cov>=4.0",
            "ruff>=0.1",
            "mypy>=1.0",
        ],
    },
    python_requires=">=3.10",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Framework :: FastAPI",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    keywords="fastapi starlette middleware throttling rate-limiting gradual delay",
    project_urls={
        "Bug Reports": "https://github.com/DhruvSimform/fastapi_gradual_throttle/issues",
        "Source": "https://github.com/DhruvSimform/fastapi_gradual_throttle",
        "Documentation": "https://github.com/DhruvSimform/fastapi_gradual_throttle#readme",
    },
)
