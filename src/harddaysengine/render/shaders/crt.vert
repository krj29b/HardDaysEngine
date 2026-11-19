#version 330 core
// Clean-room CRT presentation vertex shader (ARCHITECTURE.md §13.4.1).

in vec2 in_pos;
in vec2 in_uv;
out vec2 v_uv;

void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    v_uv = in_uv;
}
