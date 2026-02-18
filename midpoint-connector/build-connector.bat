@echo off
echo ====================================
echo Building REST Gateway Connector
echo ====================================
echo.

if not exist gradlew.bat (
    echo ERROR: gradlew.bat not found!
    echo Please ensure you are in the project root directory.
    pause
    exit /b 1
)

echo [1/3] Cleaning previous build...
call gradlew.bat clean

echo.
echo [2/3] Running tests...
call gradlew.bat test

echo.
echo [3/3] Building JAR...
call gradlew.bat jar

echo.
echo ====================================
echo Build Complete!
echo ====================================
echo.
echo JAR Location:
dir /b build\libs\*.jar 2>nul
if errorlevel 1 (
    echo ERROR: JAR not found in build\libs\
) else (
    echo.
    echo To install in Midpoint Docker:
    echo docker cp build\libs\connector-restgateway-1.0.0-SNAPSHOT.jar midpoint:/opt/midpoint/var/icf-connectors/
    echo docker restart midpoint
)

echo.
pause
