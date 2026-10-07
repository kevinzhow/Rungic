cmake_minimum_required(VERSION 3.22)
project(RungicSuggestions LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 REQUIRED COMPONENTS Core DBus Concurrent Gui Qml Quick QuickControls2 Test)
find_package(KF6WindowSystem 6.19 REQUIRED)
find_package(KF6Config REQUIRED)
find_package(KF6I18n REQUIRED)
qt_standard_project_setup(REQUIRES 6.5)
qt_policy(SET QTP0004 NEW)
# One catalog (po/<lang>/rungic-suggestions.po) for the service, its model and the QML module, which
# plasmashell and the voice assistant load: i18n() in C++ never falls back to the host's domain.
add_compile_definitions(TRANSLATION_DOMAIN="rungic-suggestions")
add_library(caremodel STATIC model.cpp model.h briefing.cpp briefing.h usage.cpp usage.h claude_code.cpp claude_code.h collector.cpp collector.h layout.cpp layout.h)
target_link_libraries(caremodel PUBLIC Qt6::Core KF6::ConfigCore KF6::I18n)
qt_add_executable(rungic-suggestions main.cpp service.cpp service.h)
target_link_libraries(rungic-suggestions PRIVATE caremodel Qt6::DBus Qt6::Concurrent)
set(module_dir "${CMAKE_BINARY_DIR}/qml/com/rungic/suggestions")
qt_add_qml_module(rungicsuggestions
    URI com.rungic.suggestions VERSION 1.0
    PLUGIN_TARGET rungicsuggestions OUTPUT_DIRECTORY "${module_dir}"
    SOURCES client.cpp client.h
    QML_FILES qml/UsageText.js qml/PixelAgent.qml qml/AgentWidget.qml qml/AgentUsageDetails.qml qml/SuggestionStack.qml qml/AgentMark.qml qml/BriefingCard.qml qml/WidgetGallery.qml qml/PresentationTracker.qml qml/SuggestionCard.qml qml/SuggestionsFeed.qml qml/SuggestionsWidget.qml)
target_link_libraries(rungicsuggestions PRIVATE Qt6::Core Qt6::DBus Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2 KF6::WindowSystem KF6::I18n)
install(TARGETS rungic-suggestions DESTINATION bin)
# Agent usage providers (docs/research/95): Claude Code, read from its own files, shown once it is used here.
qt_add_executable(rungic-agent-usage-claude-code claude_code_main.cpp)
target_link_libraries(rungic-agent-usage-claude-code PRIVATE caremodel)
install(TARGETS rungic-agent-usage-claude-code DESTINATION libexec)
install(FILES agent-usage/claude-code.json DESTINATION share/rungic/agent-usage/providers)
# The Claude Spark mark, Anthropic's trademark, unmodified, to identify Claude (provenance/agent-usage-icons-20260930).
install(FILES agent-usage/icons/claude-code.svg DESTINATION share/rungic/agent-usage/icons)
install(TARGETS rungicsuggestions LIBRARY DESTINATION "${QT6_INSTALL_QML}/com/rungic/suggestions")
install(FILES "${module_dir}/qmldir" "${module_dir}/rungicsuggestions.qmltypes" DESTINATION "${QT6_INSTALL_QML}/com/rungic/suggestions")
install(DIRECTORY applet/ DESTINATION share/plasma/plasmoids/com.rungic.suggestions)
# The widgets' icons in Add Widgets.
install(FILES icons/rungic-agent-suggestions.svg icons/rungic-agent-usage.svg DESTINATION share/icons/hicolor/scalable/apps)
install(DIRECTORY agent-applet/ DESTINATION share/plasma/plasmoids/com.rungic.agent)
ki18n_install(po)
enable_testing()
qt_add_executable(care-tests tests.cpp)
target_link_libraries(care-tests PRIVATE caremodel Qt6::Test)
target_compile_definitions(care-tests PRIVATE CARE_TESTDATA="${CMAKE_CURRENT_SOURCE_DIR}/testdata")
add_test(NAME care-model COMMAND care-tests)
qt_add_executable(care-client-tests client-tests.cpp client.cpp client.h)
target_link_libraries(care-client-tests PRIVATE Qt6::Test Qt6::DBus Qt6::Gui Qt6::Qml Qt6::Quick
    KF6::WindowSystem KF6::I18n)
add_test(NAME care-client COMMAND dbus-run-session -- $<TARGET_FILE:care-client-tests>)
# The widgets in every state (docs/87's gallery, for the home screen): offscreen screenshots with
# QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software rungic-suggestions-gallery --shot FILE.
qt_add_executable(rungic-suggestions-gallery gallery.cpp)
target_link_libraries(rungic-suggestions-gallery PRIVATE Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2)
install(TARGETS rungic-suggestions-gallery DESTINATION bin)
qt_add_executable(care-preview preview.cpp)
target_link_libraries(care-preview PRIVATE Qt6::Test Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2)
