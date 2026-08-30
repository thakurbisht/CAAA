from setuptools import setup, find_packages

setup(
    name="caaa",
    version="0.4.0",
    description="Continuous Access Assurance Agent — a measured identity governance control",
    packages=find_packages(exclude=["tests"]),
    python_requires=">=3.10",
    install_requires=[],
    extras_require={"dev": ["pytest>=7.0"]},
)
