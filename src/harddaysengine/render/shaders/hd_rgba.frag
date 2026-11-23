#version 330 core
// True-colour RGBA mode fragment shader with CPU-derived fade/flash tint (ARCHITECTURE.md §14.6.1).

uniform sampler2D u_diffuse_tex;
uniform vec3 u_fade_tint;
uniform vec4 u_flash_mix;

in vec2 v_uv;
out vec4 f_color;

void main() {
    vec4 d = texture(u_diffuse_tex, v_uv);
    vec3 faded = d.rgb * u_fade_tint;
    vec3 rgb = clamp(mix(faded, u_flash_mix.rgb, u_flash_mix.a), 0.0, 1.0);
    f_color = vec4(rgb, d.a);
}
