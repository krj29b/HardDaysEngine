#version 330 core
// Clean-room separable 9-tap Gaussian blur for widescreen surround (ARCHITECTURE.md §13.5).

uniform sampler2D u_input_tex;
uniform vec2 u_in_origin;
uniform vec2 u_in_size;
uniform vec2 u_in_tex_size;
uniform vec2 u_step_px;

in vec2 v_uv;
out vec4 f_color;

vec3 sample_clamped(vec2 px) {
    vec2 clamped = clamp(px, vec2(0.5), u_in_size - vec2(0.5));
    vec2 uv = vec2(
        (u_in_origin.x + clamped.x) / u_in_tex_size.x,
        1.0 - (u_in_origin.y + clamped.y) / u_in_tex_size.y
    );
    return texture(u_input_tex, uv).rgb;
}

void main() {
    vec2 base_px = vec2(v_uv.x, 1.0 - v_uv.y) * u_in_size;
    const float W0 = 0.204164;
    const float W1 = 0.180174;
    const float W2 = 0.123832;
    const float W3 = 0.066282;
    const float W4 = 0.027631;

    vec3 acc = sample_clamped(base_px) * W0;
    acc += (sample_clamped(base_px + u_step_px * 1.0) + sample_clamped(base_px - u_step_px * 1.0)) * W1;
    acc += (sample_clamped(base_px + u_step_px * 2.0) + sample_clamped(base_px - u_step_px * 2.0)) * W2;
    acc += (sample_clamped(base_px + u_step_px * 3.0) + sample_clamped(base_px - u_step_px * 3.0)) * W3;
    acc += (sample_clamped(base_px + u_step_px * 4.0) + sample_clamped(base_px - u_step_px * 4.0)) * W4;

    f_color = vec4(clamp(acc, vec3(0.0), vec3(1.0)), 1.0);
}
