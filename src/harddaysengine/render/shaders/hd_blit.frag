#version 330 core
// FBO-to-FBO copy fragment shader for HD compositor (ARCHITECTURE.md §14.6.2).

uniform sampler2D u_diffuse_tex;

in vec2 v_uv;
out vec4 f_color;

void main() {
    f_color = texture(u_diffuse_tex, v_uv);
}
