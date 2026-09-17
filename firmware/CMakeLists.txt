# Переходная сборка через ESP-IDF: тот же исходный код, что и у Arduino-сборки
# (XiaoVoiceRecorder/), собирается idf.py с Arduino ESP32 3.3.11 как компонентом.
#
#   idf.py set-target esp32s3
#   idf.py build                       # XIAO Sense
#   idf.py -DRECORDER_BOARD=reva build # Recorder RevA
#
# Критерий этого этапа — поведение устройства не меняется: setup()/loop() те
# же, Arduino SD/WiFi/Preferences те же. Замена подсистем на native IDF идёт
# потом, по одной (firmware/README_RU.md).
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(recorder)
