# Сборка через ESP-IDF: тот же исходный код, что и у Arduino-сборки
# (XiaoVoiceRecorder/), собирается idf.py с Arduino ESP32 как компонентом
# (версия — main/idf_component.yml). Основная прошивка платы с 12 окт 2026.
#
#   bash tools/idf.sh build                       # XIAO Sense
#   bash tools/idf.sh -DRECORDER_BOARD=reva build # Recorder RevA
#   bash tools/idf.sh -p COM5 flash
#
# Критерий этого этапа — поведение устройства не меняется: setup()/loop() те
# же, Arduino SD/WiFi/Preferences те же. Замена подсистем на native IDF идёт
# потом, по одной (firmware/README_RU.md).
cmake_minimum_required(VERSION 3.16)

# Флаги компилятора — через файл ответов. Ядро Arduino тянет за собой ~300
# каталогов заголовков, и на Windows строка вызова переходит предел 32 767
# символов (15 окт 2026: 36 228 при проекте в длинном каталоге).
set(CMAKE_NINJA_FORCE_RESPONSE_FILE ON CACHE BOOL "Windows command line limit" FORCE)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# Флаги, которые Arduino-сборка XIAO_ESP32S3 берёт из boards.txt (USBMode=hwcdc,
# CDCOnBoot, PSRAM=opi), а не из sdkconfig. Без первых двух Serial уходит на
# UART0 вместо USB: журнал и команды на COM-порту пропадают. Задаются на всю
# сборку, потому что объект Serial объявляет само ядро (HWCDC.cpp).
idf_build_set_property(COMPILE_DEFINITIONS "ARDUINO_USB_MODE=1" APPEND)
idf_build_set_property(COMPILE_DEFINITIONS "ARDUINO_USB_CDC_ON_BOOT=1" APPEND)
idf_build_set_property(COMPILE_DEFINITIONS "BOARD_HAS_PSRAM" APPEND)
project(recorder)
