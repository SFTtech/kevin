#!/bin/bash
docker build --target=kevin-standalone -t kevin-standalone .
docker build --target=kevin-inception -t kevin-inception .
