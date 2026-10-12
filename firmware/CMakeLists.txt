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
# Флаги, которые Arduino-сборка XIAO_ESP32S3 берёт из boards.txt (USBMode=hwcdc,
# CDCOnBoot, PSRAM=opi), а не из sdkconfig. Без первых двух Serial уходит на
# UART0 вместо USB: журнал и команды на COM-порту пропадают. Задаются на всю
# сборку, потому что объект Serial объявляет само ядро (HWCDC.cpp).
idf_build_set_property(COMPILE_DEFINITIONS "ARDUINO_USB_MODE=1" APPEND)
idf_build_set_property(COMPILE_DEFINITIONS "ARDUINO_USB_CDC_ON_BOOT=1" APPEND)
idf_build_set_property(COMPILE_DEFINITIONS "BOARD_HAS_PSRAM" APPEND)
project(recorder)
