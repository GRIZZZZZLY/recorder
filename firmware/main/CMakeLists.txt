# Один translation unit: main.cpp включает три .ino подряд — ровно то, что
# делает сборщик Arduino, склеивая скетч. Прототипы Arduino дописывает сам, здесь
# их заменяет порядок объявлений в файлах (все кросс-файловые функции объявлены
# в начале XiaoVoiceRecorder.ino).
idf_component_register(SRCS "main.cpp"
                       INCLUDE_DIRS "." "../../XiaoVoiceRecorder")

# Профиль платы (BoardConfig.h): -DRECORDER_BOARD=reva при вызове idf.py.
set(RECORDER_BOARD "sense" CACHE STRING "Профиль платы: sense | reva")
if(RECORDER_BOARD STREQUAL "reva")
  target_compile_definitions(${COMPONENT_LIB} PRIVATE RECORDER_BOARD_REVA)
elseif(NOT RECORDER_BOARD STREQUAL "sense")
  message(FATAL_ERROR "RECORDER_BOARD=${RECORDER_BOARD}: ожидается sense или reva")
endif()

# Arduino-скетч собирается с -Wall без -Werror; здешние предупреждения ядра
# (например, про volatile) — не наши, не превращать в ошибки.
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Wno-unused-parameter)
