cmake_minimum_required(VERSION 3.22)
project(RungicAgentScreen LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 REQUIRED COMPONENTS Core DBus Gui GuiPrivate Qml Quick WaylandClient)
find_package(LayerShellQt REQUIRED)
find_package(KF6I18n REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(WaylandClient REQUIRED IMPORTED_TARGET wayland-client)
qt_standard_project_setup(REQUIRES 6.5)
qt_add_executable(rungic-agent-screen-window main.cpp agentscreen.cpp agentscreen.h floater.cpp floater.h phonekeyboard.h)
qt_add_qml_module(rungic-agent-screen-window URI com.rungic.agentscreen VERSION 1.0 QML_FILES qml/Main.qml qml/TeamBoard.qml qml/FullTouch.qml qml/FloatingKeyboard.qml)
target_link_libraries(rungic-agent-screen-window PRIVATE Qt6::Core Qt6::DBus Qt6::Gui Qt6::GuiPrivate Qt6::Qml Qt6::Quick Qt6::WaylandClient
    LayerShellQt::Interface KF6::I18n KF6::I18nQml PkgConfig::WaylandClient)
install(TARGETS rungic-agent-screen-window DESTINATION libexec)
# Catalogs: po/zh_CN/rungic-agent-screen.po (the window) and
# plasma_com.rungic.quicksetting.desktopmode.po (the quick setting; Plasma Mobile gives each quick
# setting the domain plasma_<plugin id>).
ki18n_install(po)
# The fullscreen keyboard's style (FloatingKeyboard), where Qt Virtual Keyboard looks for styles:
# QtQuick/VirtualKeyboard/Styles/<name>/style.qml under an import path of the window's own.
install(FILES vkb/rungic/style.qml DESTINATION lib/rungic-agent-screen/qml/QtQuick/VirtualKeyboard/Styles/rungic)
