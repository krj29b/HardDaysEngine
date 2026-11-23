#version 330 core
// Indexed R8 mode fragment shader for HD compositor (ARCHITECTURE.md §14.6.1).

uniform sampler2D u_index_tex;
uniform sampler2D u_live_palette;
uniform int u_transparent_zero;

in vec2 v_uv;
out vec4 f_color;

void main() {
    int idx = int(texture(u_index_tex, v_uv).r * 255.0 + 0.5) & 31;
    if (u_transparent_zero == 1 && idx == 0) {
        discard;
    }
    f_color = texelFetch(u_live_palette, ivec2(idx, 0), 0);
}
