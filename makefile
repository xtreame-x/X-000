CXX = g++
CXXFLAGS = -O2 -std=c++17
LDFLAGS = -lpsapi

SRC = src/sysinfo.cpp
OUT = exe/sysinfo.exe

all:
	$(CXX) $(CXXFLAGS) $(SRC) -o $(OUT) $(LDFLAGS)

clean:
	del exe\sysinfo.exe